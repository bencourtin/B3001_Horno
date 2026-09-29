# -*- coding: utf-8 -*-
"""
=====================================================================
 B-3001 · Pipeline completo: limpieza, EDA, detenciones, creep y
 preparación de dataset para modelado (predicción / anomalías / riesgo)
=====================================================================
 Estructura:
   1. CONFIG            : parámetros de limpieza, detección y creep
   2. carga_y_limpieza(): xlsx -> DataFrame horario limpio + flags
   3. estados_y_detenciones(): RUN/STOP/TRANS + episodios de detención
   4. eda()             : calidad, saturación, excursiones, desvíos
   5. creep()           : Larson-Miller + Robinson por tubo
   6. features()        : dataset largo (tidy) ML-ready para los 3 criterios
   7. correlaciones()   : matriz de correlación de las features
 Ejecución: python b3001_pipeline.py <ruta_xlsx>
=====================================================================
"""
import sys, json
import numpy as np
import pandas as pd

# ------------------------------------------------------------------
# 1. CONFIG
# ------------------------------------------------------------------
SAT_LIMITE   = 700.0      # >700 °C -> saturación (clamp del historiador)
RUN_TH       = 250.0      # mediana CONV >= 250 °C -> operación
STOP_TH      = 150.0      # mediana CONV <  150 °C -> detención fría
GAP_CIERRE_H = 6          # huecos <=6 h dentro de un STOP se cierran
MERGE_H      = 24         # detenciones separadas <24 h se fusionan
MIN_STOP_H   = 4          # mínimo de horas frías reales para contar episodio

# --- Creep (material T9, API 530; parámetros del usuario) ---
CLM, A_MAT, FCR   = 20.946, 8.15e5, 1.0
T_LIM             = 705.0
DO, T_MIN, T_PROM = 108.0, 12.1, 12.1
CA                = 3.0
P_MPA             = 57.2 * 0.0981
VIDA_DISENO_H     = 100_000.0
TMT_NORMAL, TMT_DISENO, TMT_UMBRAL = 634.0, 705.0, 840.0
DM      = DO - T_PROM
SIGMA_H = (P_MPA * DM) / (2 * T_PROM)                       # 22,24 MPa
# LMP a la tensión de operación, anclado al punto de diseño (iso-esfuerzo):
LMP_SIGMA = (TMT_DISENO + 273.15) * (CLM + np.log10(VIDA_DISENO_H)) / 1000

# --- Mapeo tag -> (paso, punto). P1.1/P2.1 del archivo original -> P3/P4 ---
MAPPING = {
 'TI_30021.pv':('P1','CONV'),
 'TI_30017A.pv':('P1','T1 SP'),'TI_30017D.pv':('P1','T1 SO'),
 'TI_30017B.pv':('P1','T5 SP'),'TI_30017E.pv':('P1','T5 SO'),
 'TI_30017C.pv':('P1','T10 NP'),'TI_30017F.pv':('P1','T10 NO'),
 'TI_30022.pv':('P2','CONV'),
 'TI_30018A.pv':('P2','T1 NP'),'TI_30018D.pv':('P2','T1 NO'),
 'TI_30018B.pv':('P2','T5 NP'),'TI_30018E.pv':('P2','T5 NO'),
 'TI_30018C.pv':('P2','T10 SP'),'TI_30018F.pv':('P2','T10 SO'),
 'TI_30033.pv':('P3','CONV'),
 'TI_30031A.pv':('P3','T1 SP'),'TI_30031D.pv':('P3','T1 SO'),
 'TI_30031B.pv':('P3','T5 SP'),'TI_30031E.pv':('P3','T5 SO'),
 'TI_30031C.pv':('P3','T10 NP'),'TI_30031F.pv':('P3','T10 NO'),
 'TI_30034.pv':('P4','CONV'),
 'TI_30032A.pv':('P4','T1 NP'),'TI_30032D.pv':('P4','T1 NO'),
 'TI_30032B.pv':('P4','T5 NP'),'TI_30032E.pv':('P4','T5 NO'),
 'TI_30032C.pv':('P4','T10 SP'),'TI_30032F.pv':('P4','T10 SO'),
 'TC_30028.pv':('CTRL','P4'),'TC_30027.pv':('CTRL','P3'),
 'TC_30020.pv':('CTRL','P2'),'TC_30019.pv':('CTRL','P1'),
}
TAGS  = list(MAPPING)
CONV  = {p:[c for c,(pp,n) in MAPPING.items() if pp==p and n=='CONV'][0] for p in ['P1','P2','P3','P4']}
TUBES = {p:[c for c,(pp,n) in MAPPING.items() if pp==p and n!='CONV'] for p in ['P1','P2','P3','P4']}
CTRL  = {v[1]:k for k,v in MAPPING.items() if v[0]=='CTRL'}
NOMBRE = lambda c: f"{MAPPING[c][0]} {MAPPING[c][1]}"

# ------------------------------------------------------------------
# 2. CARGA Y LIMPIEZA
# ------------------------------------------------------------------
def carga_y_limpieza(ruta_xlsx):
    """xlsx -> (vals, sat). Etiquetas del historiador ([-11059], etc.) -> NaN;
    >700 °C -> flag de saturación y NaN en vals; grilla horaria continua."""
    df = pd.read_excel(ruta_xlsx, sheet_name=0, header=0)
    df = df.iloc[3:].rename(columns={'Unnamed: 0':'ts'})          # 3 filas de metadatos
    df['ts'] = pd.to_datetime(df['ts'], errors='coerce')
    df = (df.dropna(subset=['ts'])
            .drop_duplicates('ts', keep='first')
            .sort_values('ts').set_index('ts'))
    for c in TAGS:                                                 # texto -> NaN
        df[c] = pd.to_numeric(df[c], errors='coerce')
    grid = pd.date_range(df.index.min(), df.index.max(), freq='h') # huecos explícitos
    df = df.reindex(grid)
    sat  = df[TAGS] > SAT_LIMITE
    vals = df[TAGS].where(~sat)          # saturación fuera de las estadísticas de T
    return vals, sat

# ------------------------------------------------------------------
# 3. ESTADO DE OPERACIÓN Y DETENCIONES
# ------------------------------------------------------------------
def _episodios(mask, min_h=1):
    """Segmentos True contiguos de una serie booleana -> [(ini, fin, horas)]."""
    grp = (mask != mask.shift()).cumsum()
    out = []
    for _, idx in mask.groupby(grp).groups.items():
        if mask.loc[idx].iloc[0] and len(idx) >= min_h:
            out.append((idx[0], idx[-1], len(idx)))
    return out

def estados_y_detenciones(vals):
    """Estado horario RUN/TRANS/STOP/NODATA + tabla de detenciones."""
    op_ref = vals[list(CONV.values())].median(axis=1)
    op_ref = op_ref.fillna(vals[[t for p in TUBES for t in TUBES[p]]].median(axis=1))
    state = pd.Series(np.where(op_ref >= RUN_TH, 'RUN',
                      np.where(op_ref < STOP_TH, 'STOP', 'TRANS')), index=vals.index)
    state[op_ref.isna()] = 'NODATA'

    s  = (state == 'STOP').astype(int)
    s2 = s.rolling(2*GAP_CIERRE_H+1, center=True, min_periods=1).max()  # dilatación
    eps = []
    for a, b, _ in _episodios(s2.astype(bool)):
        seg = s.loc[a:b]
        if seg.sum() >= MIN_STOP_H:
            eps.append([a, b, (b-a).total_seconds()/3600+1, op_ref.loc[a:b].min()])
    merged = []                                                    # fusión <24 h
    for e in sorted(eps):
        if merged and (e[0]-merged[-1][1]).total_seconds()/3600 < MERGE_H:
            merged[-1][1] = e[1]
            merged[-1][2] = (e[1]-merged[-1][0]).total_seconds()/3600+1
            merged[-1][3] = min(merged[-1][3], e[3])
        else:
            merged.append(e)
    stops = pd.DataFrame(merged, columns=['inicio','fin','horas','t_min'])
    stops['dias'] = stops.horas/24
    return op_ref, state, stops

# ------------------------------------------------------------------
# 4. EDA
# ------------------------------------------------------------------
def eda(vals, sat, state, stops):
    run = state == 'RUN'
    tt  = [t for p in TUBES for t in TUBES[p]]
    calidad = pd.DataFrame({'nan_pct': (vals.isna() & ~sat).mean()*100,
                            'h_sat': sat.sum(), 'h_sat_run': sat[run].sum()})
    # episodios de saturación por tag (>=3 h)
    rows = [(NOMBRE(c), a, b, h) for c in sat.columns if sat[c].any()
            for a, b, h in _episodios(sat[c], min_h=3)]
    sat_ep = pd.DataFrame(rows, columns=['sensor','inicio','fin','horas'])
    # excursiones y ranking térmico en operación
    excur = pd.DataFrame({'h_600_650': ((vals[tt]>600)&(vals[tt]<=650))[run].sum(),
                          'h_650_700':  (vals[tt]>650)[run].sum()})
    stats = vals[tt][run].agg(['median', lambda s: s.quantile(.95), 'max']).T
    stats.columns = ['P50','P95','max']
    # residuales tubo vs mediana de su paso (base de anomalías)
    resid = pd.DataFrame({c: vals[c]-vals[cols].median(axis=1)
                          for p, cols in TUBES.items() for c in cols})
    disp = state.groupby(state.index.year).apply(lambda x: (x=='RUN').mean()*100)
    return dict(calidad=calidad, sat_ep=sat_ep, excursiones=excur,
                ranking=stats, resid=resid, disponibilidad=disp)

# ------------------------------------------------------------------
# 5. CREEP: LARSON-MILLER + ROBINSON
# ------------------------------------------------------------------
def t_ruptura(T_c):
    """t_r(T) a sigma_h, extrapolación iso-esfuerzo API 530 (LMP en SI)."""
    return 10 ** (LMP_SIGMA*1000/(np.asarray(T_c)+273.15) - CLM)

def creep(vals, sat, state, T_sat_equiv=SAT_LIMITE):
    """Fracción de vida de Robinson por tubo. Horas saturadas evaluadas a
    T_sat_equiv (700 = cota inferior; subirla para sensibilidad)."""
    run = state == 'RUN'
    h_run = run.sum()
    filas = []
    for c in [t for p in TUBES for t in TUBES[p]]:
        v, s = vals[c][run], sat[c][run]
        D_obs = (1.0/t_ruptura(v.dropna())).sum()
        D_sat = s.sum()/t_ruptura(T_sat_equiv)
        cob   = (v.notna().sum()+s.sum())/h_run
        D_est = (D_obs+D_sat)/max(cob, 1e-9)          # extrapola huecos
        filas.append((NOMBRE(c), int(s.sum()), round(cob*100,1), D_obs, D_est,
                      (1-D_est)*t_ruptura(TMT_NORMAL)/8760))
    return (pd.DataFrame(filas, columns=['tubo','h_sat','cobertura_pct',
                                          'D_medido','D_estimado','vida_rem_anios_634'])
              .sort_values('D_estimado', ascending=False))

# ------------------------------------------------------------------
# 6. FEATURES (dataset tidy ML-ready para los 3 criterios)
# ------------------------------------------------------------------
def features(vals, sat, state, stops, freq='h'):
    """Dataset largo: 1 fila = (timestamp, tubo). Ver README para el detalle
    de cada bloque de variables y su criterio asociado."""
    run   = (state == 'RUN')
    # horas desde el último arranque (ciclo operativo)
    arranques = pd.Series(0, index=vals.index)
    for r in stops.itertuples():
        arranques.loc[r.fin] = 1
    ciclo = arranques.cumsum()
    h_desde_arranque = vals.index.to_series().groupby(ciclo).cumcount()

    frames = []
    for p, cols in TUBES.items():
        med_paso = vals[cols].median(axis=1)
        for c in cols:
            T = vals[c]
            f = pd.DataFrame(index=vals.index)
            f['tubo'], f['paso'] = NOMBRE(c), p
            # --- base ---
            f['TMT'] = T
            f['sat'] = sat[c].astype(int)
            f['run'] = run.astype(int)
            f['conv_paso'] = vals[CONV[p]]
            f['ctrl_paso'] = vals[CTRL[p]]
            f['h_desde_arranque'] = h_desde_arranque.values
            # --- criterio 1: predicción térmica ---
            for L in (1, 3, 6, 24):
                f[f'lag_{L}h'] = T.shift(L)
            f['roll_mean_24h'] = T.rolling(24, min_periods=12).mean()
            f['roll_std_24h']  = T.rolling(24, min_periods=12).std()
            f['rampa_1h']      = T.diff()
            f['rampa_6h']      = T.diff(6)/6
            f['target_T_24h']  = T.shift(-24)          # objetivo t+24 h
            # --- criterio 2: anomalías / desviaciones ---
            f['resid_paso'] = T - med_paso             # desvío vs su paso
            mu  = f['resid_paso'].rolling(24*30, min_periods=24*7).median()
            sd  = f['resid_paso'].rolling(24*30, min_periods=24*7).std()
            f['z_resid_30d']  = (f['resid_paso']-mu)/sd  # z robusto móvil
            f['flatline_12h'] = (T.rolling(12).std() < 0.05).astype(int)
            f['anomalia']     = ((f['z_resid_30d'].abs() > 4) & run).astype(int)
            # --- criterio 3: riesgo / vida remanente ---
            dano = pd.Series(0.0, index=vals.index)
            m = run & T.notna();  dano[m] = 1.0/t_ruptura(T[m])
            dano[run & sat[c]]    = 1.0/t_ruptura(SAT_LIMITE)
            f['dano_1h']    = dano                       # consumo instantáneo
            f['D_acum']     = dano.cumsum()              # Robinson acumulado
            f['h650_acum']  = ((T > 650) & run).cumsum() # exposición severa
            f['sat_acum']   = (sat[c] & run).cumsum()
            frames.append(f)
    out = pd.concat(frames).reset_index().rename(columns={'index':'ts'})
    return out

# ------------------------------------------------------------------
# 7. CORRELACIONES ENTRE FEATURES
# ------------------------------------------------------------------
FEATS_NUM = ['TMT','conv_paso','ctrl_paso','h_desde_arranque','lag_1h','lag_24h',
             'roll_mean_24h','roll_std_24h','rampa_1h','rampa_6h','resid_paso',
             'z_resid_30d','dano_1h','D_acum','h650_acum','sat_acum',
             'target_T_24h','anomalia','sat']

def correlaciones(feat, metodo='spearman', solo_run=True, muestra=200_000):
    d = feat[feat.run == 1] if solo_run else feat
    if len(d) > muestra:
        d = d.sample(muestra, random_state=0)
    return d[FEATS_NUM].corr(method=metodo)

# ------------------------------------------------------------------
if __name__ == '__main__':
    ruta = sys.argv[1] if len(sys.argv) > 1 else 'Dataset_T_C_B-3001.xlsx'
    vals, sat = carga_y_limpieza(ruta)
    op_ref, state, stops = estados_y_detenciones(vals)
    resultados_eda = eda(vals, sat, state, stops)
    tabla_creep = creep(vals, sat, state)
    feat = features(vals, sat, state, stops)
    corr = correlaciones(feat)
    stops.to_csv('detenciones.csv', index=False)
    tabla_creep.to_csv('robinson_vida_tubos.csv', index=False)
    feat.to_parquet('b3001_features.parquet')
    corr.to_csv('correlaciones_features.csv')
    print(stops); print(tabla_creep.head(10)); print(corr.round(2))
