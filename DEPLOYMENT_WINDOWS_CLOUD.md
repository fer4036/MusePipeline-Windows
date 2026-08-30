# MusePipeline Windows + Cloud Deployment

## Arquitectura

La GUI del investigador y del operador corre en Render como servicio FastAPI publico.

La laptop Windows corre localmente el EdgeAgent. Ese agente se conecta por WebSocket seguro a Render y controla el pipeline Bluetooth/BrainFlow de la diadema Muse.

Flujo:

Investigador -> Render Cloud GUI -> WebSocket -> EdgeAgent Windows -> Muse por Bluetooth

Operador/participante -> Render Cloud GUI -> WebSocket -> EdgeAgent Windows -> SQLite local

## Render

Tipo de servicio: Web Service

Build Command:

pip install -r requirements.txt

Start Command:

python -m muse_web.cloud_app

Variables de entorno:

MUSE_CLOUD_RESEARCHER_TOKEN=dev-researcher
MUSE_CLOUD_OPERATOR_TOKEN=dev-operator
MUSE_CLOUD_AGENT_TOKENS={"lab-windows-01":"clave-agente-01"}

Health check path:

/health

URL investigador:

https://TU-SERVICIO.onrender.com/?role=pipeline&token=dev-researcher

URL operador:

https://TU-SERVICIO.onrender.com/?role=operator&token=dev-operator

## Windows EdgeAgent

En la laptop con la Muse:

python -m muse_web.edge_agent `
  --cloud-url "wss://TU-SERVICIO.onrender.com/ws/agent/lab-windows-01" `
  --agent-id "lab-windows-01" `
  --agent-token "clave-agente-01" `
  --max-devices 1 `
  --sessions-root "$env:USERPROFILE\MuseResearch\sessions"

## Branches de desarrollo actuales

### feature/windows-cloud-agent

Contiene la base operativa Windows/cloud:

- GUI cloud en Render para investigador y operador.
- EdgeAgent local por WebSocket saliente.
- Control remoto de preparacion, grabacion, cierre y ground truth.
- Backend local Windows con BrainFlow/Bleak.
- SQLite local como fuente canonica y exportacion CSV al cerrar sesion.

Comando base del agente local:

```powershell
python -m muse_web.edge_agent `
  --cloud-url "wss://musepipeline.onrender.com/ws/agent/lab-windows-01" `
  --agent-id "lab-windows-01" `
  --agent-token "clave-agente-01" `
  --max-devices 1 `
  --sessions-root "$env:USERPROFILE\MuseResearch\sessions"
```

### feature/ml-training-xai

Contiene el pipeline offline de entrenamiento:

- `muse_ml.dataset`: convierte CSV/SQLite exportado a ventanas.
- `muse_ml.features`: extrae band powers, ratios EEG y features PPG.
- `muse_ml.train`: entrena modelos con Leave-One-Subject-Out.
- Modelos comparados: ElasticNet, Random Forest, XGBoost y comparador
  Riemannian.
- XAI con SHAP global y explicaciones locales por prediccion.

Instalar dependencias ML:

```powershell
python -m pip install -r requirements-ml.txt
```

Crear dataset de entrenamiento:

```powershell
python -m muse_ml.dataset `
  --input-dir subject_data `
  --output-dir ml_output `
  --window-seconds 60 `
  --stride-seconds 30
```

Entrenar y generar XAI:

```powershell
python -m muse_ml.train `
  --input-dir ml_output `
  --output-dir ml_output\models
```

### feature/realtime-cognitive-staet

Contiene el inicio del monitoreo en tiempo real. Nota: el nombre de la branch
tiene un typo en `staet`; se puede renombrar despues a
`feature/realtime-cognitive-state`.

Implementacion agregada:

- `muse_ml.realtime`: lee la ventana reciente del SQLite activo.
- Reutiliza exactamente el mismo extractor de band powers/ratios EEG y PPG del
  entrenamiento.
- Usa ventanas de 60 s y actualizacion cada 30 s por defecto.
- Publica a la GUI cloud solamente si se activa `--enable-cognitive-cloud`.
- Si no se configura modelo, la GUI muestra que las features estan listas pero
  que falta cargar el predictor.
- Si se configura `xgboost_final.joblib`, reporta score SCEM estimado, nivel
  bajo/medio/alto, confianza, muestras disponibles y factores SHAP/importancia.

Comando del agente con monitoreo cognitivo habilitado:

```powershell
python -m muse_web.edge_agent `
  --cloud-url "wss://musepipeline.onrender.com/ws/agent/lab-windows-01" `
  --agent-id "lab-windows-01" `
  --agent-token "clave-agente-01" `
  --max-devices 1 `
  --sessions-root "$env:USERPROFILE\MuseResearch\sessions" `
  --enable-cognitive-cloud `
  --cognitive-model "ml_output\models\xgboost_final.joblib" `
  --cognitive-window-seconds 60 `
  --cognitive-update-seconds 30
```

Si solo quieres probar la GUI sin publicar predicciones cognitivas, omite
`--enable-cognitive-cloud`.

## Resultados actuales del entrenamiento ML

Extraccion ejecutada con `subject_data`, ventanas de 60 s y stride de 30 s:

```text
subjects: 11
windows: 690
features: 167
target: scem_score_recomputed
validation: leave-one-subject-out
```

Metricas LOSO globales actuales:

| Modelo | MAE | RMSE | R2 | Spearman | Ventanas |
|---|---:|---:|---:|---:|---:|
| ElasticNet | 0.827 | 0.990 | -0.899 | -0.361 | 690 |
| Random Forest | 0.863 | 1.042 | -1.104 | -0.555 | 690 |
| XGBoost | 0.783 | 0.959 | -0.780 | -0.574 | 690 |
| Riemannian + Ridge | 0.761 | 0.997 | -0.927 | -0.627 | 690 |

Interpretacion: el pipeline ya entrena y genera explicaciones, pero la
generalizacion LOSO todavia no es suficiente para uso cientifico final. Los R2
negativos indican que, con pocos sujetos y etiquetas SCEM limitadas, el modelo
generaliza peor que una prediccion media en sujetos no vistos. Por ahora debe
usarse como prototipo de monitoreo y para guiar recoleccion de mas datos, no
como estimador validado.

Consultar resultados:

```powershell
Get-Content ml_output\models\training_summary.json

Import-Csv ml_output\models\metrics_loso.csv |
  Where-Object { $_.fold_subject -eq "__overall__" } |
  Format-Table model,mae,rmse,r2,spearman,n -AutoSize

Import-Csv ml_output\models\xgboost_shap_global.csv |
  Select-Object -First 20 |
  Format-Table feature,mean_abs_shap -AutoSize

Import-Csv ml_output\models\xgboost_shap_local_top10.csv |
  Select-Object -First 30 |
  Format-Table window_id,feature,shap_value,feature_value -AutoSize
```

Archivos principales:

- `ml_output\models\metrics_loso.csv`: accuracy/regresion por fold y global.
- `ml_output\models\predictions_loso.csv`: prediccion vs SCEM real por ventana.
- `ml_output\models\training_summary.json`: resumen de datos/modelos.
- `ml_output\models\xgboost_final.joblib`: modelo sugerido para inferencia.
- `ml_output\models\xgboost_shap_global.csv`: factores globales del modelo.
- `ml_output\models\xgboost_shap_local_top10.csv`: factores por prediccion.

## Bluetooth en Windows

Arquitectura recomendada:

1 laptop Windows + 1 adaptador Bluetooth Intel activo + 1 diadema Muse + 1 EdgeAgent

No se recomienda por ahora conectar varias Muse a la misma laptop Windows usando varios adaptadores USB Bluetooth.

## Verificacion

Probar Render:

Invoke-WebRequest -UseBasicParsing https://TU-SERVICIO.onrender.com/health | Select-Object -ExpandProperty Content

Respuesta esperada:

{"ok":true,"agents":0,"configured_agent_ids":["lab-windows-01"],"configuration_error":""}

## Problemas comunes

Si la pagina dice "Clave de investigador invalida":

- Verifica que la URL tenga ?role=pipeline&token=TU_TOKEN_REAL
- El token debe coincidir exactamente con MUSE_CLOUD_RESEARCHER_TOKEN en Render.

Si el EdgeAgent marca HTTP 403:

- El agent-token no coincide.
- El agent-id no existe en MUSE_CLOUD_AGENT_TOKENS.
- MUSE_CLOUD_AGENT_TOKENS no es JSON valido.

Formato correcto:

{"lab-windows-01":"clave-agente-01"}

Si marca HTTP 404:

- La URL del WebSocket apunta al servicio equivocado.
- Usa el dominio real de Render con wss://.

Si marca HTTP 500:

- Revisa /health.
- Probablemente MUSE_CLOUD_AGENT_TOKENS esta mal formado.
