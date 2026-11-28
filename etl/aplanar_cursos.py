"""Aplana la jerarquía padre/hijo de Cursos para facilitar su lectura en MongoDB."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def ultimo_snapshot() -> str:
    archivos = list((RAIZ / "data/raw").glob("cursos_*.json"))
    fechas = sorted(p.stem.removeprefix("cursos_") for p in archivos)
    if not fechas:
        raise FileNotFoundError("No hay cursos_*.json en data/raw")
    return fechas[-1]


def aplanar(snapshot: str) -> list[dict]:
    origen = RAIZ / "data/raw" / f"cursos_{snapshot}.json"
    cursos = json.loads(origen.read_text(encoding="utf-8"))
    por_clave = {(str(c["Anyo"]), str(c["codigo"])): c for c in cursos}
    resultado = []
    for curso in cursos:
        cadena, actual, vistos = [], curso, set()
        while actual:
            clave = (str(actual["Anyo"]), str(actual["codigo"]))
            if clave in vistos:
                raise ValueError(f"Ciclo en la jerarquía de cursos: {clave}")
            vistos.add(clave)
            cadena.append(actual)
            padre = str(actual.get("padre", "")).strip()
            actual = por_clave.get((clave[0], padre)) if padre else None
        nombres = [n["nombre_cas"] for n in reversed(cadena)]
        resultado.append({
            "anyo": str(curso["Anyo"]), "codigo": str(curso["codigo"]),
            "nombre": curso.get("nombre_cas"), "nombre_val": curso.get("nombre_val"),
            "abreviatura": curso.get("abreviatura"),
            "ensenanza": curso.get("ensenanza"), "padre": curso.get("padre"),
            "jerarquia": nombres,
            "familia": nombres[0] if len(nombres) > 3 else None,
            "grado": nombres[1] if len(nombres) > 2 else None,
            "ciclo": nombres[2] if len(nombres) > 2 else None,
            "nivel": nombres[-1],
        })
    return resultado


def main() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=None, help="Sufijo común de los JSON; por defecto, el más reciente")
    args = parser.parse_args()
    snapshot = args.snapshot or ultimo_snapshot()
    documentos = aplanar(snapshot)
    destino = RAIZ / "data/trusted" / f"cursos_aplanados_{snapshot}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(documentos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Cursos aplanados: {len(documentos)} -> {destino.relative_to(RAIZ)}")
    return destino


if __name__ == "__main__":
    main()
