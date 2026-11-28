"""Combina cada fila de Contenidos con su jerarquía de Cursos aplanada."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def crear_documentos(snapshot: str) -> list[dict]:
    raw = RAIZ / "data/raw"
    contenidos = json.loads((raw / f"contenidos_{snapshot}.json").read_text(encoding="utf-8"))
    cursos = json.loads((RAIZ / "data/trusted" / f"cursos_aplanados_{snapshot}.json").read_text(encoding="utf-8"))
    mapa = {(str(c["anyo"]), str(c["codigo"])): c for c in cursos}
    resultado = []
    for fila, c in enumerate(contenidos):
        anyo, curso_codigo = str(c["Anyo"]), str(c["curso"])
        curso = mapa.get((anyo, curso_codigo))
        resultado.append({
            "_id": f"{snapshot}:contenido:{fila}",
            "origen": "ITACA", "snapshot": snapshot,
            "anyo": anyo, "codigo": str(c["codigo"]),
            "nombre": c.get("nombre_cas"), "nombre_val": c.get("nombre_val"),
            "ensenanza": c.get("ensenanza"), "curso_codigo": curso_codigo,
            "curso": curso,
        })
    return resultado


def main() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    args = parser.parse_args()
    documentos = crear_documentos(args.snapshot)
    destino = RAIZ / "data/trusted" / f"modulos_con_curso_{args.snapshot}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(documentos, ensure_ascii=False, indent=2), encoding="utf-8")
    sin_curso = sum(d["curso"] is None for d in documentos)
    print(f"Módulos preparados: {len(documentos)}; sin curso asociado: {sin_curso}")
    return destino


if __name__ == "__main__":
    main()
