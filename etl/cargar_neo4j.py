"""Carga el último conjunto raw de ITACA en Neo4j.

Los JSON de la ingesta están anonimizados. La identidad de cada alumno incluye
NIA, año académico y curso; así se conservan matrículas de distintos años sin
mezclarlas. El script es repetible: usa MERGE y claves únicas.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from neo4j import GraphDatabase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / '.env')
RAW_DIR = PROJECT_ROOT / "data" / "raw"
BATCH_SIZE = 2_000


def latest_common_snapshot() -> dict[str, Path]:
    """Find the newest date for which all five raw JSON files exist."""
    prefixes = ("alumnos", "calificaciones", "contenidos", "cursos", "grupos")
    files_by_prefix: dict[str, dict[str, Path]] = {}
    for prefix in prefixes:
        candidates = {}
        for path in RAW_DIR.glob(f"{prefix}_*.json"):
            match = re.fullmatch(rf"{prefix}_(\d{{4}}-\d{{2}}-\d{{2}})\.json", path.name)
            if match:
                candidates[match.group(1)] = path
        if not candidates:
            raise FileNotFoundError(f"No hay archivos {prefix}_FECHA.json en {RAW_DIR}")
        files_by_prefix[prefix] = candidates

    common_dates = set.intersection(
        *(set(files_by_prefix[prefix]) for prefix in prefixes)
    )
    if not common_dates:
        raise FileNotFoundError("No existe una fecha común para los cinco JSON raw de ITACA")

    selected_date = max(common_dates)
    selected = {prefix: files_by_prefix[prefix][selected_date] for prefix in prefixes}
    LOGGER.info("Usando la instantánea raw %s", selected_date)
    return selected


def read_json(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        rows = json.load(file)
    if not isinstance(rows, list):
        raise ValueError(f"Se esperaba un array JSON en {path}")
    return rows


def text(value: Any) -> str:
    """Normalize source identifiers to stripped strings."""
    return "" if value is None else str(value).strip()


def nonblank(value: Any) -> str | None:
    result = text(value)
    return result or None


def as_number(value: Any) -> float | None:
    """Convert numeric grades while preserving non-numeric source codes as null."""
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def chunks(rows: list[dict[str, Any]], size: int = BATCH_SIZE) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def write_batches(session, query: str, rows: list[dict[str, Any]]) -> None:
    for batch in chunks(rows):
        session.execute_write(lambda tx, items: tx.run(query, rows=items).consume(), batch)


def create_constraints(session) -> None:
    """Protect the composite identities needed by multi-year ITACA data."""
    statements = (
        "CREATE CONSTRAINT alumno_identidad IF NOT EXISTS "
        "FOR (a:Alumno) REQUIRE (a.nia, a.anyo) IS UNIQUE",
        "CREATE CONSTRAINT modulo_identidad IF NOT EXISTS "
        "FOR (m:Modulo) REQUIRE (m.codigo, m.anyo, m.curso_codigo) IS UNIQUE",
        "CREATE CONSTRAINT grupo_identidad IF NOT EXISTS "
        "FOR (g:Grupo) REQUIRE (g.codigo, g.anyo) IS UNIQUE",
    )
    for statement in statements:
        session.run(statement).consume()


def course_hierarchy(year: str, code: str, courses: dict[tuple[str, str], dict[str, Any]]) -> list[str]:
    """Return the names from the referenced course up its parent hierarchy."""
    names: list[str] = []
    seen: set[str] = set()
    current = code
    while current and current not in seen:
        seen.add(current)
        row = courses.get((year, current))
        if row is None:
            break
        name = nonblank(row.get("nombre_cas"))
        if name:
            names.append(name)
        current = text(row.get("padre"))
    return names


def build_graph_rows(raw: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    courses_by_key = {
        (text(row.get("Anyo")), text(row.get("codigo"))): row
        for row in raw["cursos"]
        if text(row.get("Anyo")) and text(row.get("codigo"))
    }

    groups_by_key = {
        (text(row.get("Anyo")), text(row.get("codigo"))): row
        for row in raw["grupos"]
        if text(row.get("Anyo")) and text(row.get("codigo"))
    }

    # A content code may be reused in different courses, so the module identity
    # includes year and course code as well as the content code.
    modules_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in raw["contenidos"]:
        year = text(row.get("Anyo"))
        code = text(row.get("codigo"))
        course_code = text(row.get("curso"))
        if not (year and code and course_code):
            continue
        hierarchy = course_hierarchy(year, course_code, courses_by_key)
        course = courses_by_key.get((year, course_code), {})
        modules_by_key[(year, code, course_code)] = {
            "codigo": code,
            "anyo": year,
            "curso_codigo": course_code,
            "nombre": nonblank(row.get("nombre_cas")) or code,
            "curso": nonblank(course.get("nombre_cas")) or course_code,
            # Content rows point to the cycle-level course, unlike student
            # rows, which point to a year/level below the cycle.
            "ciclo": hierarchy[0] if hierarchy else None,
            "grado": hierarchy[1] if len(hierarchy) > 1 else None,
            "familia": hierarchy[2] if len(hierarchy) > 2 else None,
        }

    # A student is one node per anonymized NIA and academic year. The source
    # can list several course codes for one student-year; keep those codes as
    # context, choose the most frequent one for the profile, and preserve all
    # valid group memberships.
    profiles_by_student: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    turns_by_student: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    students: set[tuple[str, str]] = set()
    memberships: set[tuple[str, str, str]] = set()
    for row in raw["alumnos"]:
        year = text(row.get("Anyo"))
        nia = text(row.get("NIA"))
        course_code = text(row.get("curso"))
        if not (year and nia):
            continue
        key = (nia, year)
        students.add(key)
        if course_code:
            profiles_by_student[key][course_code] += 1
        turno = nonblank(row.get("turno"))
        if turno:
            turns_by_student[key][turno] += 1
        group_code = nonblank(row.get("grupo"))
        if group_code and (year, group_code) in groups_by_key:
            memberships.add((nia, year, group_code))

    group_rows = [
        {
            "codigo": code,
            "anyo": year,
            "nombre": nonblank(row.get("nombre")) or code,
        }
        for (year, code), row in groups_by_key.items()
    ]

    student_rows = []
    for nia, year in sorted(students):
        course_counts = profiles_by_student[(nia, year)]
        course_code = (
            sorted(course_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            if course_counts
            else ""
        )
        hierarchy = course_hierarchy(year, course_code, courses_by_key) if course_code else []
        turno_counts = turns_by_student[(nia, year)]
        turno = (
            sorted(turno_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            if turno_counts
            else None
        )
        student_rows.append(
            {
                "nia": nia,
                "anyo": year,
                "cursos_codigo": sorted(course_counts),
                "curso": hierarchy[0] if hierarchy else None,
                "ciclo": hierarchy[1] if len(hierarchy) > 1 else None,
                "grado": hierarchy[2] if len(hierarchy) > 2 else None,
                "familia": hierarchy[3] if len(hierarchy) > 3 else None,
                "turno": turno,
            }
        )
    module_rows = list(modules_by_key.values())
    membership_rows = [
        {"nia": nia, "anyo": year, "grupo": group}
        for nia, year, group in sorted(memberships)
    ]

    # Match students by anonymized NIA/year, and modules by their source
    # content/year/course key. This preserves the grade-table's actual course
    # reference even when the student roster has another course code.
    # This preserves parallel CURSA relationships for distinct evaluation/type
    # records while making a repeat load idempotent.
    grade_rows: list[dict[str, Any]] = []
    missing_student = 0
    missing_module = 0
    missing_both = 0
    student_keys = students
    module_keys = set(modules_by_key)
    for row in raw["calificaciones"]:
        year = text(row.get("Anyo"))
        nia = text(row.get("alumno"))
        course_code = text(row.get("curso"))
        module_code = text(row.get("contenido"))
        student_exists = (nia, year) in student_keys
        module_exists = (year, module_code, course_code) in module_keys
        if not student_exists and not module_exists:
            missing_both += 1
            continue
        if not student_exists:
            missing_student += 1
            continue
        if not module_exists:
            missing_module += 1
            continue
        grade_rows.append(
            {
                "nia": nia,
                "anyo": year,
                "curso_codigo": course_code,
                "modulo": module_code,
                "evaluacion": nonblank(row.get("evaluacion")) or "sin_evaluacion",
                "bloque_contenido": nonblank(row.get("bloque_contenido")) or "",
                "tipo_nota": nonblank(row.get("tipo_nota")) or "",
                "nota": as_number(row.get("nota_numerica")),
            }
        )

    counts = {
        "alumnos": len(student_rows),
        "grupos": len(group_rows),
        "modulos": len(module_rows),
        "pertenece_a": len(membership_rows),
        "cursa": len(grade_rows),
        "calificaciones_sin_alumno": missing_student,
        "calificaciones_sin_modulo": missing_module,
        "calificaciones_sin_ambos": missing_both,
    }
    return {
        "alumnos": student_rows,
        "grupos": group_rows,
        "modulos": module_rows,
        "pertenece_a": membership_rows,
        "cursa": grade_rows,
    }, counts


def load_graph(uri: str, username: str, password: str, rows: dict[str, list[dict[str, Any]]]) -> None:
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            create_constraints(session)

            write_batches(
                session,
                """
                UNWIND $rows AS row
                MERGE (g:Grupo {codigo: row.codigo, anyo: row.anyo})
                SET g.nombre = row.nombre
                """,
                rows["grupos"],
            )
            write_batches(
                session,
                """
                UNWIND $rows AS row
                MERGE (m:Modulo {
                    codigo: row.codigo,
                    anyo: row.anyo,
                    curso_codigo: row.curso_codigo
                })
                SET m.nombre = row.nombre,
                    m.curso = row.curso,
                    m.ciclo = row.ciclo,
                    m.grado = row.grado,
                    m.familia = row.familia
                """,
                rows["modulos"],
            )
            write_batches(
                session,
                """
                UNWIND $rows AS row
                MERGE (a:Alumno {
                    nia: row.nia,
                    anyo: row.anyo
                })
                SET a.cursos_codigo = row.cursos_codigo,
                    a.curso = row.curso,
                    a.ciclo = row.ciclo,
                    a.grado = row.grado,
                    a.familia = row.familia,
                    a.turno = row.turno
                """,
                rows["alumnos"],
            )
            write_batches(
                session,
                """
                UNWIND $rows AS row
                MATCH (a:Alumno {
                    nia: row.nia,
                    anyo: row.anyo
                })
                MATCH (g:Grupo {codigo: row.grupo, anyo: row.anyo})
                MERGE (a)-[:PERTENECE_A]->(g)
                """,
                rows["pertenece_a"],
            )
            write_batches(
                session,
                """
                UNWIND $rows AS row
                MATCH (a:Alumno {
                    nia: row.nia,
                    anyo: row.anyo
                })
                MATCH (m:Modulo {
                    codigo: row.modulo,
                    anyo: row.anyo,
                    curso_codigo: row.curso_codigo
                })
                MERGE (a)-[r:CURSA {
                    evaluacion: row.evaluacion,
                    bloque_contenido: row.bloque_contenido,
                    tipo_nota: row.tipo_nota
                }]->(m)
                SET r.nota = row.nota
                """,
                rows["cursa"],
            )
    finally:
        driver.close()


def main() -> None:
    snapshot = latest_common_snapshot()
    raw = {name: read_json(path) for name, path in snapshot.items()}
    graph_rows, counts = build_graph_rows(raw)

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    username = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise RuntimeError("Define NEO4J_PASSWORD en el entorno o en el archivo .env local")

    load_graph(uri, username, password, graph_rows)
    for name, count in counts.items():
        LOGGER.info("%s: %s", name, count)
    LOGGER.info("Carga de Neo4j completada.")


if __name__ == "__main__":
    main()




