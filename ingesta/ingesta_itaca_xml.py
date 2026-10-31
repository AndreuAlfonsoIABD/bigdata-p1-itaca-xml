import hashlib
import json
import xml.etree.ElementTree as ET

from datetime import date
from pathlib import Path

CAMPOS = {
    "alumnos": [
        "NIA",
        "sexo",
        "nacionalidad",
        "provincia",
        "municipio",
        "ensenanza",
        "curso",
        "grupo",
        "turno",
        "linea",
        "modalidad",
        "repite",
        "estado_matricula",
        "tipo_matricula",
        "fecha_matricula",
        "fecha_ingreso_centro",
    ],
    "calificaciones": [
        "evaluacion",
        "alumno",
        "ensenanza",
        "curso",
        "contenido",
        "bloque_contenido",
        "nota_numerica",
        "tipo_nota",
    ],
    "contenidos": [
        "codigo",
        "nombre_cas",
        "nombre_val",
        "ensenanza",
        "curso",
    ],
    "cursos": [
        "codigo",
        "nombre_cas",
        "nombre_val",
        "abreviatura",
        "ensenanza",
        "padre",
    ],
    "grupos": [
        "codigo",
        "nombre",
        "ensenanza",
        "turno",
        "aula",
        "capacidad",
        "linea",
        "modalidad",
    ],
}

RUTA_EXPORTACIONES = Path("data/exportaciones")
RUTA_RAW = Path("data/raw")

CURSOS_ACADEMICOS = [
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
]

ARCHIVOS_XML = {
    "alumnos": "alumnos.xml",
    "calificaciones": "calificaciones.xml",
    "contenidos": "contenidos.xml",
    "cursos": "cursos.xml",
    "grupos": "grupos.xml",
}

def anonimizar_nia(nia: str) -> str:
    return hashlib.sha256(str(nia).encode()).hexdigest()[:10]

def leer_xml(ruta: str, tabla: str):
    arbol = ET.parse(ruta)
    raiz = arbol.getroot()

    anyo = raiz.attrib.get("curso")

    registros = raiz.find(tabla)

    if registros is None:
        return []

    datos = []

    for registro in registros:
        fila = {"Anyo": anyo}

        for campo in CAMPOS[tabla]:
            fila[campo] = registro.attrib.get(campo)

        datos.append(fila)

    return datos

def anonimizar_datos(datos: list, tabla: str):
    for fila in datos:
        if tabla == "alumnos":
            nia = fila.get("NIA")

            if nia is not None:
                fila["NIA"] = anonimizar_nia(nia)

        elif tabla == "calificaciones":
            nia = fila.get("alumno")

            if nia is not None:
                fila["alumno"] = anonimizar_nia(nia)

    return datos

def procesar_datos():
    datos_totales = {
        tabla: []
        for tabla in ARCHIVOS_XML
    }

    for curso in CURSOS_ACADEMICOS:
        ruta_curso = RUTA_EXPORTACIONES / curso

        for tabla, archivo in ARCHIVOS_XML.items():
            ruta_xml = ruta_curso / archivo

            datos = leer_xml(ruta_xml, tabla)
            datos = anonimizar_datos(datos, tabla)

            datos_totales[tabla].extend(datos)

    return datos_totales

def guardar_datos(datos_totales):
    RUTA_RAW.mkdir(parents=True, exist_ok=True)

    fecha = date.today().isoformat()

    for tabla, datos in datos_totales.items():
        ruta_json = RUTA_RAW / f"{tabla}_{fecha}.json"

        with open(ruta_json, "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=4)

        print(f"Archivo generado: {ruta_json}")

if __name__ == "__main__":
    datos_totales = procesar_datos()
    guardar_datos(datos_totales)

    print("Proceso de ingesta finalizado.")