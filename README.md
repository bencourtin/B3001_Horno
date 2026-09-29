# Tesis — Horno B-3001

Análisis de salud, detección de anomalías y vida remanente (RUL) de los tubos del horno de proceso **B-3001**, a partir de datos horarios del historiador (2008–2026, 24 tubos, 4 pasos).

**Pregunta central:** ¿qué tan sano está el horno hoy y qué tubo hay que vigilar primero?

**Respuesta corta:** el horno opera con normalidad la mayor parte del tiempo, pero dos tubos del Paso 3 (**31B** y **31C**) concentran casi todo el daño por creep del paso, originado en su mayoría por saturaciones prolongadas del sensor (>700 °C) y no por la operación normal. El tubo **31B** es el crítico, con una vida remanente estimada entre 37 y 93 años según el método. La proyección a octubre de 2028 no anticipa una parada por fluencia, con un margen de 6× contra el criterio de rotura (ΔT crítico = +86 °C).

## Estructura del repo

```
.
├── EDA_B3001.ipynb                  Análisis exploratorio principal (detenciones, rampas, saturación, creep, RUL)
├── EDA_B3001_Colab.ipynb            Versión del EDA para ejecutar en Google Colab
├── B3001_features.ipynb             Construcción del dataset de features (v1)
├── B3001_features_v2.ipynb          Construcción del dataset de features (v2)
├── B3001_modelo_baseline.ipynb      Modelos baseline (Random Forest / XGBoost) y proyección a 2028
├── B3001_FNN-SVV.ipynb              Modelo de red neuronal (en desarrollo)
├── functions/
│   └── b3001_pipeline.py            Pipeline de limpieza, features y cálculo de daño (creep)
├── files/
│   └── Flujo_Preparacion_Modelado_B3001.md   Documentación del pipeline y diccionario de features
├── reportes/
│   ├── informe_eda_b3001.md/.html   Informe de EDA: detenciones, rampas, saturación, creep y RUL
│   ├── informe_modelos.md/.html     Informe de modelado: baseline, importancia de variables, proyección 2028
│   ├── *.csv                        Resultados tabulares (correlaciones, importancia, proyecciones, métricas)
│   └── graficos/, fig_*.png         Figuras usadas en los informes
├── data/                            Datos crudos y cache (ignorado por git, ver .gitignore)
└── features/                        Datasets de features generados (parquet, ignorado por git)
```

> `data/` y `features/` no se versionan por su tamaño (ver `.gitignore`); se regeneran corriendo el pipeline (`functions/b3001_pipeline.py`) sobre el Excel del historiador.

## Flujo de trabajo

```
xlsx historiador
   │ carga y limpieza, estados RUN/STOP, detenciones
   ▼
EDA_B3001.ipynb  ──►  reportes/informe_eda_b3001.md   (salud del horno, creep, RUL por tubo)
   │
   │ features()
   ▼
B3001_features(_v2).ipynb  ──►  features/b3001_features.parquet
   │
   ▼
B3001_modelo_baseline.ipynb  ──►  reportes/informe_modelos.md   (modelos, importancia, proyección 2028)
```

Detalle del pipeline de features y diccionario de columnas en [`files/Flujo_Preparacion_Modelado_B3001.md`](files/Flujo_Preparacion_Modelado_B3001.md).

## Informes

- [Informe EDA — salud del horno, creep y RUL](reportes/informe_eda_b3001.md)
- [Informe de modelos — baseline y proyección 2028](reportes/informe_modelos.md)

## Datos y norma

- Fuente: historiador del horno B-3001, `Dataset T°C B-3001.xlsx` (no versionado).
- Norma / material de referencia: API 530, Larson-Miller, acero T9.
