# Flujo de preparación del dataset — B-3001 → modelado

Código fuente: `b3001_pipeline.py`. Salida principal: `b3001_features.parquet` (formato tidy: **3.714.048 filas × 26 columnas**, 1 fila = timestamp × tubo, 24 tubos × 154.752 horas).

## Flujo general

```
xlsx historiador
   │ 1. carga_y_limpieza()      etiquetas texto→NaN · >700°C→flag sat · grilla horaria · dedup
   ▼
vals (°C) + sat (bool)
   │ 2. estados_y_detenciones() estado RUN/TRANS/STOP/NODATA · episodios de detención
   ▼
state + stops
   │ 3. eda()                   calidad · saturación · excursiones · residuales · disponibilidad
   │ 4. creep()                 t_r(T) por LMP anclado (705°C/100.000h) · Robinson por tubo
   ▼
   5. features()                dataset tidy ML-ready (bloques por criterio)
   │ 6. correlaciones()         Spearman entre features (solo RUN, muestra 200k)
   ▼
b3001_features.parquet + correlaciones_features.csv + fig9
```

Decisiones de diseño que importan aguas abajo: (a) el formato largo permite entrenar un solo modelo global con `tubo`/`paso` como variables categóricas, en lugar de 24 modelos; (b) la saturación se conserva como **flag y no como temperatura**, para que ningún modelo aprenda del clamp de 700 °C como si fuera físico; (c) el filtro `run == 1` es obligatorio para los tres criterios — entrenar con enfriamientos de detención contamina cualquier modelo térmico; (d) `h_desde_arranque` reinicia en cada fin de detención y captura el ciclo operativo (coquización progresiva).

## Diccionario de features por criterio

**Base (todas):** `ts, tubo, paso, TMT, sat, run, conv_paso, ctrl_paso, h_desde_arranque`.

**Criterio 1 — Predicción térmica.** Autoregresivas y de contexto: `lag_1h, lag_3h, lag_6h, lag_24h, roll_mean_24h, roll_std_24h, rampa_1h, rampa_6h` y el objetivo `target_T_24h` (TMT a t+24 h; cambiar el horizonte es un `shift`). Modelos sugeridos en orden de complejidad: baseline persistente (lag_24h), gradient boosting global (LightGBM con tubo categórico), y solo después secuenciales (LSTM/TCN) si el boosting no basta. Validación **siempre temporal** (train hasta año X, test posterior), nunca aleatoria: las correlaciones altas entre lags harían trivial un split aleatorio.

**Criterio 2 — Anomalías y desviaciones.** `resid_paso` (tubo menos mediana de su paso: quita el efecto de carga común y deja solo lo idiosincrático), `z_resid_30d` (z-score móvil de 30 días del residual: detecta cambios de régimen del propio tubo), `flatline_12h` (sensor congelado) y la etiqueta débil `anomalia` (|z|>4 en operación). Ruta sugerida: partir con reglas sobre z_resid (ya operativas), luego IsolationForest/autoencoder sobre el vector de residuales del paso completo para capturar anomalías multivariadas (un tubo que sube mientras sus vecinos bajan). Importante: el residual separa proceso de sensor — desvío sostenido y suave = distribución de flujo/coque; salto abrupto aislado = termocupla.

**Criterio 3 — Riesgo, criticidad y vida remanente.** `dano_1h` (1/t_r(TMT), consumo instantáneo de vida Robinson), `D_acum` (fracción acumulada), `h650_acum` (exposición severa) y `sat_acum` (horas ciegas acumuladas — en este horno es en sí misma una variable de riesgo, ver informe de creep). El índice de criticidad natural es una combinación de nivel (D_acum), velocidad (dano_1h promedio móvil anual) e incertidumbre (sat_acum). Para vida remanente probabilística: reconstruir primero la T real de las horas saturadas por regresión contra `conv_paso`/`ctrl_paso` en horas no saturadas, y propagar la incertidumbre de esa reconstrucción a la distribución de D — esto conecta con tu trabajo PINN, donde la física (LMP) actúa como término de pérdida.

## Correlaciones (Spearman, solo horas RUN)

Matriz completa en `correlaciones_features.csv` y `fig9_correlaciones.png`. Lo relevante para el modelado:

**Para predicción:** `target_T_24h` correlaciona 0,92 con TMT actual y lag_1h, 0,90 con roll_mean_24h y 0,88 con lag_24h. Traducción: fuerte persistencia diaria — el baseline "mañana = hoy" será difícil de batir y es la vara mínima para cualquier modelo. `resid_paso` aporta señal adicional (0,78) parcialmente independiente del nivel absoluto: conviene incluirlo como predictor.

**Para anomalías:** la etiqueta `anomalia` correlaciona débilmente con todo (máx 0,16 con roll_std_24h) — y eso es correcto: una anomalía útil es por definición lo que no se explica con el nivel térmico. Confirma que el residual normalizado es un eje casi ortogonal al de temperatura y que la detección debe basarse en él, no en umbrales absolutos de TMT.

**Para riesgo:** `dano_1h` es función monótona de TMT (ρ=1,0 por construcción vía t_r), pero lo informativo es el resto: correlaciona 0,28 con `sat` y solo 0,25 con `D_acum` — el daño instantáneo y el acumulado son dimensiones distintas de criticidad (un tubo puede tener alto stock de daño y bajo flujo actual, o al revés), por lo que el índice de riesgo debe usar ambas. La correlación moderada de `z_resid_30d` con dano_1h (0,22) sugiere que las desviaciones idiosincráticas detectadas por el criterio 2 son un precursor parcial del consumo de vida del criterio 3: los tres criterios se encadenan (anomalía → sobrecalentamiento → daño).

Advertencia de colinealidad: TMT, lags y roll_mean forman un bloque con ρ>0,9 entre sí; para modelos lineales o interpretabilidad (SHAP) conviene elegir un representante por bloque o regularizar.
