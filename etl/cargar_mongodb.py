"""Carga la última instantánea ITACA en MongoDB de forma repetible."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pymongo import MongoClient, ASCENDING

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aplanar_cursos import aplanar  # noqa: E402
from modulos_con_curso import crear_documentos  # noqa: E402


def ultima_instantanea() -> str:
    raw = RAIZ / "data/raw"
    conjuntos = []
    for nombre in ("alumnos", "calificaciones", "contenidos", "cursos"):
        conjuntos.append({p.stem.removeprefix(nombre + "_") for p in raw.glob(f"{nombre}_*.json")})
    comunes = set.intersection(*conjuntos) if conjuntos else set()
    if not comunes:
        raise FileNotFoundError("No hay una instantánea común de alumnos, calificaciones, contenidos y cursos")
    return sorted(comunes)[-1]


def leer(nombre: str, snapshot: str) -> list[dict]:
    return json.loads((RAIZ / "data/raw" / f"{nombre}_{snapshot}.json").read_text(encoding="utf-8"))


def numero(valor):
    if valor is None or not str(valor).strip():
        return None
    try:
        return float(str(valor).strip().replace(",", "."))
    except ValueError:
        return None


def etiqueta(curso: dict | None, codigo: str) -> str:
    if not curso:
        return codigo
    partes = curso.get("jerarquia", [])
    return " / ".join(partes) if partes else str(curso.get("nombre") or codigo)


def preparar_alumnos(filas: list[dict], cursos: dict, snapshot: str) -> list[dict]:
    docs = []
    for i, a in enumerate(filas):
        anyo, codigo = str(a.get("Anyo", "")), str(a.get("curso", ""))
        ctx = cursos.get((anyo, codigo))
        docs.append({
            "_id": f"{snapshot}:alumno:{i}", "origen": "ITACA", "snapshot": snapshot,
            "fila_origen": i, "nia": a.get("NIA"), "anyo": anyo,
            "curso_codigo": codigo, "curso": etiqueta(ctx, codigo),
            "ensenanza": a.get("ensenanza"), "grupo": a.get("grupo"),
            "turno": a.get("turno"), "sexo": a.get("sexo"),
            "nacionalidad": a.get("nacionalidad"), "provincia": a.get("provincia"),
            "municipio": a.get("municipio"), "linea": a.get("linea"),
            "modalidad": a.get("modalidad"), "repite": a.get("repite"),
            "estado_matricula": a.get("estado_matricula"),
            "tipo_matricula": a.get("tipo_matricula"),
            "fecha_matricula": a.get("fecha_matricula"),
            "fecha_ingreso_centro": a.get("fecha_ingreso_centro"),
        })
    return docs


def preparar_calificaciones(filas: list[dict], cursos: dict, snapshot: str, alumnos: list[dict]) -> list[dict]:
    grupos_por_alumno: dict[tuple[str, str], set[str]] = {}
    for alumno in alumnos:
        clave = (str(alumno.get("NIA", "")), str(alumno.get("Anyo", "")))
        grupo = str(alumno.get("grupo", "")).strip()
        if grupo:
            grupos_por_alumno.setdefault(clave, set()).add(grupo)
    docs = []
    for i, c in enumerate(filas):
        anyo, codigo = str(c.get("Anyo", "")), str(c.get("curso", ""))
        ctx = cursos.get((anyo, codigo))
        docs.append({
            "_id": f"{snapshot}:calificacion:{i}", "origen": "ITACA", "snapshot": snapshot,
            "fila_origen": i, "nia": c.get("alumno"), "anyo": anyo,
            "curso_codigo": codigo, "curso": etiqueta(ctx, codigo),
            # Copia derivada para agregar por grupo sin un $lookup por cada calificación.
            "grupos": sorted(grupos_por_alumno.get((str(c.get("alumno", "")), anyo), set())),
            "modulo": c.get("contenido"), "bloque_contenido": c.get("bloque_contenido"),
            "ensenanza": c.get("ensenanza"), "nota": numero(c.get("nota_numerica")),
            "evaluacion": c.get("evaluacion"), "tipo_nota": c.get("tipo_nota"),
        })
    return docs


def insertar_lotes(coleccion, documentos: list[dict], tamano: int = 1000) -> None:
    for inicio in range(0, len(documentos), tamano):
        coleccion.insert_many(documentos[inicio:inicio + tamano], ordered=False)


def main() -> None:
    snapshot = os.environ.get("ITACA_SNAPSHOT") or ultima_instantanea()
    for nombre in ("alumnos", "calificaciones", "contenidos", "cursos"):
        if not (RAIZ / "data/raw" / f"{nombre}_{snapshot}.json").exists():
            raise FileNotFoundError(f"Falta {nombre}_{snapshot}.json")

    trusted = RAIZ / "data/trusted"
    trusted.mkdir(parents=True, exist_ok=True)
    cursos_docs = aplanar(snapshot)
    (trusted / f"cursos_aplanados_{snapshot}.json").write_text(
        json.dumps(cursos_docs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    modulo_docs = crear_documentos(snapshot)
    (trusted / f"modulos_con_curso_{snapshot}.json").write_text(
        json.dumps(modulo_docs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    mapa_cursos = {(c["anyo"], c["codigo"]): c for c in cursos_docs}
    raw_alumnos = leer("alumnos", snapshot)
    alumno_docs = preparar_alumnos(raw_alumnos, mapa_cursos, snapshot)
    calificacion_docs = preparar_calificaciones(leer("calificaciones", snapshot), mapa_cursos, snapshot, raw_alumnos)

    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
    db_name = os.environ.get("MONGO_DATABASE", "itaca")
    cliente = MongoClient(uri, serverSelectionTimeoutMS=5000)
    cliente.admin.command("ping")
    db = cliente[db_name]
    lotes = {"alumnos": alumno_docs, "modulos": modulo_docs, "calificaciones": calificacion_docs}
    try:
        for nombre, docs in lotes.items():
            col = db[nombre]
            # Reemplaza solo filas de esta carga; otros documentos de la colección se conservan.
            col.delete_many({"origen": "ITACA"})
            if docs:
                insertar_lotes(col, docs)

        db.alumnos.create_index([("nia", ASCENDING), ("anyo", ASCENDING), ("curso_codigo", ASCENDING)], name="alumno_contexto")
        db.modulos.create_index([("codigo", ASCENDING), ("anyo", ASCENDING), ("curso_codigo", ASCENDING)], name="modulo_contexto")
        db.calificaciones.create_index([("curso", ASCENDING), ("evaluacion", ASCENDING)], name="curso_evaluacion")
        db.calificaciones.create_index([("nia", ASCENDING), ("anyo", ASCENDING), ("curso_codigo", ASCENDING), ("modulo", ASCENDING), ("evaluacion", ASCENDING)], name="calificacion_contexto")
        db.calificaciones.create_index([("grupos", ASCENDING), ("evaluacion", ASCENDING)], name="grupo_evaluacion")

        print(f"Instantánea: {snapshot}")
        for nombre, docs in lotes.items():
            print(f"{nombre}: {db[nombre].count_documents({'origen': 'ITACA'})} documentos (origen: {len(docs)})")
        print(f"Notas vacías/no numéricas normalizadas a null: {sum(d['nota'] is None for d in calificacion_docs)}")
        print(f"Calificaciones sin grupo asociado en la instantánea: {sum(not d['grupos'] for d in calificacion_docs)}")
        print("Índices creados: alumno_contexto, modulo_contexto, curso_evaluacion, calificacion_contexto, grupo_evaluacion")
    finally:
        cliente.close()


if __name__ == "__main__":
    main()
