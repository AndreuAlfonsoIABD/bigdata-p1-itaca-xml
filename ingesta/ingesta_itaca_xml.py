import hashlib
import json
import logging
import xml.etree.ElementTree as ET

from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

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

def anonimizar_nia(nia: str, cache: dict) -> str:
    if nia not in cache:
        cache[nia] = hashlib.sha256(
            str(nia).encode()
        ).hexdigest()[:10]

    return cache[nia]

def leer_xml(ruta: str, tabla: str):
    try:
        arbol = ET.parse(ruta)
        raiz = arbol.getroot()

    except FileNotFoundError:
        logger.error(f"Archivo no encontrado: {ruta}")
        return []

    except ET.ParseError:
        logger.error(f"Error al parsear el archivo XML: {ruta}")
        return []

    anyo = raiz.attrib.get("curso")

    registros = raiz.find(tabla)

    if registros is None:
        logger.warning(
            f"No se encontraron registros para la tabla '{tabla}' en el archivo: {ruta}"
        )
        return []

    datos = []

    for registro in registros:
        fila = {"Anyo": anyo}

        for campo in CAMPOS[tabla]:
            fila[campo] = registro.attrib.get(campo)

        datos.append(fila)

    logger.info(
        f"Se han leído {len(datos)} registros de la tabla '{tabla}' del archivo: {ruta}"
    )

    return datos

def anonimizar_datos(datos: list, tabla: str, cache: dict):
    for fila in datos:
        if tabla == "alumnos":
            nia = fila.get("NIA")

            if nia is not None:
                fila["NIA"] = anonimizar_nia(nia, cache)

        elif tabla == "calificaciones":
            nia = fila.get("alumno")

            if nia is not None:
                fila["alumno"] = anonimizar_nia(nia, cache)

    return datos

def procesar_datos():
    datos_totales = {
        tabla: []
        for tabla in ARCHIVOS_XML
    }

    cache_nia = {}

    for curso in CURSOS_ACADEMICOS:
        ruta_curso = RUTA_EXPORTACIONES / curso

        for tabla, archivo in ARCHIVOS_XML.items():
            ruta_xml = ruta_curso / archivo

            datos = leer_xml(ruta_xml, tabla)
            datos = anonimizar_datos(
                datos, 
                tabla, 
                cache_nia,
            )

            datos_totales[tabla].extend(datos)

    return datos_totales

def validar_claves(datos_totales):
    alumnos = {
        fila["NIA"]
        for fila in datos_totales["alumnos"]
        if fila.get("NIA") is not None
    }

    contenidos = {
        fila["codigo"]
        for fila in datos_totales["contenidos"]
        if fila.get("codigo") is not None
    }

    errores_alumnos = 0;
    errores_contenidos = 0;

    for calificacion in datos_totales["calificaciones"]:
        alumno = calificacion.get("alumno")
        contenido = calificacion.get("contenido")

        if alumno not in alumnos:
            errores_alumnos += 1

        if contenido not in contenidos:
            errores_contenidos += 1

    logger.info(
        f"Validación de claves: {errores_alumnos} calificaciones con NIA no encontrado, "
        f"{errores_contenidos} calificaciones con contenido no encontrado."
    )

    if errores_alumnos == 0 and errores_contenidos == 0:
        logger.info("Todas las claves de calificaciones son válidas.")
    else:
        logger.warning(
            "Se encontraron errores en las claves de calificaciones. "
            "Revisar los registros para más detalles."
        )

    return errores_alumnos, errores_contenidos

def guardar_datos(datos_totales):
    RUTA_RAW.mkdir(parents=True, exist_ok=True)

    fecha = date.today().isoformat()

    for tabla, datos in datos_totales.items():
        ruta_json = RUTA_RAW / f"{tabla}_{fecha}.json"

        with open(ruta_json, "w", encoding="utf-8") as archivo:
            json.dump(
                datos, 
                archivo, 
                ensure_ascii=False, 
                indent=4
            )

        logger.info(
            f"Se han guardado {len(datos)} registros de la tabla '{tabla}' en el archivo: {ruta_json}"
        )

if __name__ == "__main__":
    datos_totales = procesar_datos()

    validar_claves(datos_totales)

    guardar_datos(datos_totales)

    logger.info("Proceso de ingesta y anonimización de datos completado.")