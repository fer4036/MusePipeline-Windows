# Reporte tecnico y metodologico del entrenamiento ML y monitoreo cognitivo en tiempo real

## 1. Objetivo general

Este documento describe la arquitectura tecnica y la metodologia implementada
para estimar el engagement cognitivo de estudiantes usando senales fisiologicas
capturadas con una diadema Muse. El sistema tiene dos partes principales:

1. Un pipeline offline de entrenamiento y validacion del modelo.
2. Un pipeline online de monitoreo en tiempo real integrado a la GUI cloud.

El objetivo final es estimar un estado de engagement cognitivo cada cierto
intervalo de tiempo durante una actividad educativa. La prediccion se basa en
EEG y PPG, no en texto ni en observacion manual directa.

## 2. Senales fisiologicas utilizadas

### 2.1 EEG

EEG significa electroencefalografia. Es una medicion electrica de la actividad
cerebral registrada desde el cuero cabelludo. En este proyecto se trabaja con
cuatro canales EEG de la diadema Muse.

Un canal EEG representa la senal medida por un electrodo o posicion de medicion.
Como la Muse tiene pocos canales, el modelo no puede hacer analisis espaciales
muy complejos como los que se harian con sistemas clinicos de 32, 64 o mas
electrodos. Por eso se priorizaron features simples, robustas e interpretables.

### 2.2 PPG

PPG significa fotopletismografia. Es una senal optica que permite estimar
cambios relacionados con el pulso sanguineo. A partir de PPG se pueden derivar
medidas como frecuencia cardiaca y variabilidad del pulso.

En este proyecto PPG se usa como complemento del EEG. La razon metodologica es
que el engagement cognitivo no depende solo de actividad cortical; tambien puede
relacionarse con activacion fisiologica, esfuerzo y regulacion autonomica.

## 3. Ground truth: escala SCEM

El ground truth es la referencia contra la que se entrena el modelo. En este
caso, el ground truth proviene de respuestas del estudiante a una escala Likert
basada en SCEM: Situational Cognitive Engagement Measure.

La escala tiene cuatro items:

- task_engagement
- effort
- persistence
- flow

Cada item se responde de 1 a 5. El score SCEM usado por el modelo se recalcula
como:

```text
SCEM = mean(task_engagement, effort, persistence, flow)
```

Esto significa que el valor final tambien queda entre 1 y 5. El score guardado
por la GUI se conserva para auditoria, pero el pipeline ML usa el promedio
recalculado desde los cuatro items como fuente canonica.

## 4. Estructura general del pipeline de entrenamiento

El flujo implementado es:

```text
CSV/SQLite crudo
-> muse_ml.dataset
-> ml_windows.csv
-> ml_features.csv
-> ml_labels.csv
-> muse_ml.train / muse_ml.experiments
-> validacion LOSO
-> modelos ML
-> XAI / explicabilidad
-> seleccion del modelo para realtime
```

### 4.1 CSV/SQLite crudo

Durante una sesion experimental, el sistema local guarda datos en SQLite. Al
exportar una sesion se generan CSV con:

- metadatos de sesion;
- EEG;
- PPG;
- IMU;
- respuestas SCEM;
- informacion de operador, seccion y timestamps.

SQLite es la fuente local canonica durante la adquisicion. CSV se usa para
entrenamiento, auditoria y portabilidad.

### 4.2 Ventanas temporales

EEG y PPG no se analizan muestra por muestra. En lugar de eso se dividen en
ventanas de tiempo. Una ventana agrupa varios segundos de senal para obtener
features estables.

La configuracion principal usada fue:

```text
window_seconds = 60
stride_seconds = 30
```

Esto significa:

- cada prediccion usa 60 segundos de datos;
- cada nueva ventana empieza 30 segundos despues de la anterior;
- las ventanas se traslapan parcialmente.

Tambien se probo una configuracion de 30 s / 15 s. Aunque produjo mas ventanas,
no mejoro la generalizacion. Esto es importante: mas ventanas no equivalen a mas
sujetos independientes.

## 5. Extraccion de features EEG

Una feature es una variable numerica calculada desde la senal cruda. El modelo
no recibe directamente la senal EEG completa; recibe resumenes fisiologicos.

### 5.1 Band powers

El EEG se separa en bandas de frecuencia:

| Banda | Rango aproximado | Interpretacion general |
|---|---:|---|
| Delta | 1-4 Hz | Actividad lenta |
| Theta | 4-8 Hz | Procesamiento, memoria de trabajo, esfuerzo mental |
| Alpha | 8-13 Hz | Relajacion cortical, inhibicion, estado atencional |
| Beta | 13-30 Hz | Activacion, atencion, procesamiento activo |
| Gamma baja | 30-45 Hz | Actividad rapida, posible procesamiento local |

Band power significa cuanta energia tiene la senal dentro de una banda. Por
ejemplo, alpha power mide cuanta energia aparece entre 8 y 13 Hz.

### 5.2 Potencia relativa

La potencia absoluta puede variar mucho entre sujetos por contacto del electrodo,
cabello, impedancia, postura y diferencias anatomicas. Por eso se usan tambien
potencias relativas:

```text
relative_power_band = power_band / total_power
```

Esto ayuda a comparar la distribucion de energia entre bandas, no solo la
magnitud cruda.

### 5.3 Ratios EEG

Tambien se calculan relaciones entre bandas:

- theta/alpha
- beta/alpha
- theta/beta
- beta/(alpha + theta)

Estas razones pueden capturar cambios de balance atencional o esfuerzo cognitivo.
Se usan en logaritmo para estabilizar escalas:

```text
log_ratio = log(power_a / power_b)
```

### 5.4 Features robustas

El primer modelo usaba muchas features, incluyendo covarianzas y potencias
absolutas. Con pocos sujetos esto puede causar sobreajuste: el modelo aprende
diferencias individuales en vez de patrones generalizables.

Por eso se implementaron feature sets:

- `all`: todas las features disponibles;
- `robust`: features mas estables;
- `eeg_robust`: solo EEG robusto;
- `ppg_robust`: solo PPG robusto.

El mejor resultado actual uso `eeg_robust`.

## 6. Extraccion de features PPG

Desde PPG se extraen medidas relacionadas con pulso:

- frecuencia cardiaca media;
- variabilidad de frecuencia cardiaca;
- IBI, intervalo entre latidos;
- RMSSD, variabilidad latido a latido;
- SDNN, variabilidad general del intervalo;
- pNN50, proporcion de cambios mayores a 50 ms;
- calidad de deteccion de picos;
- cantidad de canales PPG validos.

Estas features ayudan a representar activacion fisiologica y esfuerzo. En los
experimentos PPG tambien logro resultados cercanos al EEG, aunque el mejor modelo
fue EEG robusto.

## 7. Reetiquetado temporal y peso de ventanas

Las respuestas SCEM se capturan en momentos discretos, pero las senales se
registran continuamente. No todas las ventanas dentro de un intervalo tienen la
misma cercania temporal a la respuesta.

Se implemento un peso temporal:

```text
label_half_life_seconds = 180
```

Las ventanas mas cercanas a la respuesta SCEM reciben mayor peso. Las ventanas
mas lejanas no se eliminan, pero pesan menos. El minimo peso permitido es 0.25.

Esto reduce ruido metodologico porque el estado cognitivo reportado por el
estudiante probablemente representa mejor los minutos cercanos a la respuesta
que todo el bloque por igual.

## 8. Validacion Leave-One-Subject-Out

LOSO significa Leave-One-Subject-Out. Es una validacion donde se entrena con
todos los sujetos excepto uno, y se prueba en el sujeto que quedo fuera.

Ejemplo con 11 sujetos:

```text
Fold 1: entrenar con sujetos 2-11, probar en sujeto 1
Fold 2: entrenar con sujetos 1,3-11, probar en sujeto 2
...
Fold 11: entrenar con sujetos 1-10, probar en sujeto 11
```

Esta es una validacion estricta y adecuada para saber si el modelo generaliza a
personas nuevas. Es mas honesta que dividir ventanas al azar, porque ventanas
del mismo sujeto son muy parecidas entre si.

## 9. Metricas de evaluacion

### 9.1 MAE

MAE significa Mean Absolute Error. Es el error absoluto promedio:

```text
MAE = mean(abs(SCEM_real - SCEM_predicho))
```

Si MAE es 0.56, el modelo se equivoca en promedio 0.56 puntos en una escala de
1 a 5. Menor MAE es mejor.

### 9.2 RMSE

RMSE significa Root Mean Squared Error. Tambien mide error, pero castiga mas los
errores grandes:

```text
RMSE = sqrt(mean(error^2))
```

Si RMSE baja, significa que el modelo comete menos errores grandes.

### 9.3 R2

R2 mide si el modelo explica mejor la variabilidad que una prediccion promedio.

```text
R2 = 1     prediccion perfecta
R2 = 0     similar a predecir el promedio
R2 < 0     peor que predecir el promedio
```

El mejor modelo actual tiene R2 cercano a cero, todavia ligeramente negativo.
Eso significa que ya esta cerca de superar el promedio tambien en varianza
explicada, pero aun no es un modelo final.

### 9.4 Spearman

Spearman mide si el modelo ordena correctamente los estados. No pregunta si el
valor exacto fue perfecto, sino si cuando el SCEM real sube, la prediccion
tambien tiende a subir.

```text
Spearman = 1   orden perfecto
Spearman = 0   sin relacion monotona
Spearman < 0   orden invertido
```

La mejora mas importante del nuevo modelo fue que Spearman cambio de negativo a
positivo.

## 10. Baseline de comparacion

El baseline principal es `baseline_train_mean`. Este modelo no usa EEG ni PPG.
Simplemente predice el promedio SCEM de los sujetos de entrenamiento.

Este baseline es importante porque un modelo fisiologico debe demostrar que
aporta informacion adicional. Si no supera al promedio, no esta aprendiendo una
senal fisiologica transferible.

## 11. Resultados antes de la arquitectura calibrada

Con modelos directos de SCEM absoluto, el sistema entrenaba, pero no superaba al
baseline:

| Modelo | MAE | RMSE | R2 | Spearman |
|---|---:|---:|---:|---:|
| baseline_train_mean | 0.607 | 0.771 | -0.151 | -0.716 |
| bayesian_ridge | 0.609 | 0.773 | -0.159 | -0.708 |
| xgboost | 0.681 | 0.854 | -0.414 | -0.642 |

La lectura metodologica fue clara: el modelo directo no generalizaba bien entre
sujetos.

## 12. Uso de paso_1 como baseline fisiologico

Se reviso la practica `practica_1_pick_and_place.md`. El paso 1 se llama "No
tengo ojos" y tiene una duracion objetivo de 3 minutos. En este paso el alumno:

- recibe explicacion inicial;
- observa o escucha la posicion del robot;
- aprende que debe usar coordenadas;
- responde un quiz conceptual;
- aun no ejecuta rutinas motoras complejas.

Por esto, el paso 1 es viable como baseline fisiologico pasivo de tarea. No es
un baseline de reposo, porque el estudiante sigue aprendiendo informacion nueva.
La forma rigurosa de reportarlo es:

```text
baseline fisiologico de aprendizaje pasivo especifico de la tarea
```

Este baseline permite normalizar cada sujeto contra si mismo:

```text
feature_normalizada = feature_actual - feature_paso_1
```

La idea es reducir diferencias individuales de EEG/PPG que no tienen que ver con
engagement.

## 13. Arquitectura two-stage implementada

El mejor modelo actual usa una arquitectura de dos etapas.

### 13.1 Stage 1: modelo fisiologico global

El primer stage aprende una relacion entre fisiologia y cambio relativo de SCEM.

Configuracion:

```text
features = eeg_robust
baseline fisiologico = paso_1
feature selection = SelectKBest k=20
modelo = Bayesian Ridge
target = relative_scem
smoothing = rolling mean de 3 ventanas
```

En vez de predecir directamente:

```text
SCEM absoluto = 4.2
```

el modelo predice:

```text
SCEM relativo = SCEM actual - referencia del sujeto
```

Esto es mas robusto porque los sujetos pueden tener escalas internas diferentes.
Una persona puede reportar 4 como normal y otra puede reportar 3 para un estado
similar.

### 13.2 Stage 2: calibrador por sujeto

El segundo stage reconstruye el SCEM absoluto usando una primera respuesta SCEM
del operador:

```text
SCEM_estimado = primera_respuesta_SCEM + SCEM_relativo_predicho
```

Esto requiere una calibracion inicial. En tiempo real, el sistema no debe
reportar SCEM absoluto hasta que el operador haya enviado al menos una respuesta
SCEM.

## 14. Resultados de la arquitectura calibrada

La mejor configuracion fue:

```text
eeg_robust | relative_scem | bayesian_ridge | k=20 |
rolling3 | initial_subject | paso_1
```

Comparacion:

| Modelo | MAE | RMSE | R2 | Spearman | Supera baseline |
|---|---:|---:|---:|---:|---|
| baseline_train_mean | 0.607 | 0.771 | -0.151 | -0.716 | no |
| two-stage calibrado | 0.561 | 0.714 | -0.004 | 0.525 | si |

La mejora del MAE fue aproximadamente 7.7%. Mas importante aun, Spearman paso de
negativo a positivo. Esto indica que el modelo calibrado empieza a ordenar mejor
los estados de engagement.

## 14.1 Modelo de referencia para realtime

El modelo de referencia actual para el pipeline en tiempo real queda definido
como:

```text
stage1_relative_scem_stage2_calibrator.joblib
```

Este artefacto se genera con `muse_ml.experiments` y representa la arquitectura
de mejor desempeno observada hasta ahora:

```text
feature_set = eeg_robust
target_type = relative_scem
model = bayesian_ridge
feature_k = 20
smoothing = rolling3
calibration = initial_subject
physiological_baseline = paso_1
```

Por esta razon, cualquier prediccion realtime debe interpretarse bajo las mismas
condiciones metodologicas:

1. El `paso_1` debe registrarse para estimar el baseline fisiologico del sujeto.
2. El operador debe enviar al menos una respuesta SCEM para calibrar la escala
   individual.
3. La salida final debe suavizarse con las ultimas tres predicciones.
4. La GUI debe reportar que el modelo es experimental y calibrado, no una medida
   clinica o diagnostica.

## 14.2 Analisis EEG-only vs PPG-only bajo la misma arquitectura

Para evaluar si el desempeno del modelo depende mas de EEG, PPG o de la
combinacion de ambos, se compararon tres configuraciones usando exactamente la
misma arquitectura:

```text
target_type = relative_scem
model = bayesian_ridge
feature_k = 20
smoothing = rolling3
calibration = initial_subject
physiological_baseline = paso_1
window_seconds = 60
stride_seconds = 30
label_half_life_seconds = 180
validation = LOSO
```

La unica diferencia entre corridas fue el conjunto de features:

- `eeg_robust`: solo features EEG robustas.
- `ppg_robust`: solo features PPG robustas.
- `robust`: EEG + PPG robusto.

Resultados:

| Feature set | Modalidad | MAE | RMSE | R2 | Spearman | Balanced accuracy | Macro F1 | Supera baseline |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `eeg_robust` | EEG solamente | 0.561 | 0.714 | -0.004 | 0.525 | 0.714 | 0.678 | si |
| `ppg_robust` | PPG solamente | 0.561 | 0.716 | -0.009 | 0.542 | 0.714 | 0.678 | si |
| `robust` | EEG + PPG | 0.562 | 0.717 | -0.012 | 0.545 | 0.714 | 0.678 | si |
| `none` | Baseline promedio train | 0.607 | 0.771 | -0.151 | -0.716 | 0.418 | 0.299 | no |

### Lectura tecnica

Los tres modelos calibrados superaron al baseline. La diferencia entre EEG-only,
PPG-only y EEG+PPG fue pequena:

```text
EEG-only MAE = 0.5607
PPG-only MAE = 0.5614
EEG+PPG MAE = 0.5621
```

En terminos de MAE, EEG-only fue ligeramente mejor. En terminos de Spearman, la
combinacion EEG+PPG tuvo el valor mas alto, seguida muy de cerca por PPG-only:

```text
EEG-only Spearman = 0.525
PPG-only Spearman = 0.542
EEG+PPG Spearman = 0.545
```

Esto sugiere que:

1. EEG aporta una estimacion ligeramente mas precisa del valor SCEM.
2. PPG aporta informacion util para ordenar estados relativos de engagement.
3. Combinar EEG+PPG no mejora el MAE en este dataset, probablemente porque el
   numero de sujetos todavia es bajo y agregar modalidades tambien agrega ruido.

### Lectura metodologica

El resultado no debe interpretarse como "PPG reemplaza EEG" ni como "EEG no es
necesario". La conclusion rigurosa es mas cautelosa:

```text
Con el dataset actual, bajo una arquitectura calibrada por sujeto, EEG-only,
PPG-only y EEG+PPG producen desempenos muy similares. EEG-only tiene el menor
MAE, mientras que EEG+PPG tiene el mejor ordenamiento Spearman.
```

Para el pipeline realtime se conserva EEG-only como modelo de referencia porque
fue el mejor por MAE y porque el objetivo principal del sistema es engagement
cognitivo estimado desde EEG. Sin embargo, PPG debe mantenerse como modalidad
secundaria para analisis posteriores, control de calidad fisiologica y modelos
multimodales futuros.

### Implicacion para el diseno experimental

Esta comparacion indica que el valor metodologico no esta solamente en capturar
mas senales, sino en calibrarlas correctamente contra el propio sujeto. La mejora
aparecio cuando se combinaron:

- baseline fisiologico `paso_1`;
- prediccion relativa;
- calibracion SCEM inicial;
- seleccion de features dentro del fold;
- suavizado temporal.

Por tanto, antes de concluir que una modalidad es superior, se deben recolectar
mas sujetos y mantener condiciones balanceadas de engagement bajo, medio y alto.

## 15. Experimentos comparativos implementados

Se agrego `muse_ml.experiments`, que ejecuta comparaciones automaticas y genera:

- `experiment_results.csv`;
- `experiment_predictions.csv`;
- `nested_loso_selections.csv`;
- `experiment_summary.json`;
- `stage1_relative_scem_stage2_calibrator.joblib`.

La tabla final incluye:

```text
feature_set
target_type
model
smoothing
calibration
MAE
RMSE
R2
Spearman
balanced_accuracy
macro_f1
beats_baseline
```

### 15.1 Feature selection dentro del fold

Se implemento `SelectKBest` dentro del pipeline de scikit-learn. Esto es
importante porque la seleccion de features debe aprenderse solo con datos de
entrenamiento en cada fold, no con todo el dataset.

Si se seleccionaran features usando todos los sujetos antes del LOSO, habria
data leakage. Data leakage significa que informacion del sujeto de prueba entra
indirectamente al entrenamiento, inflando resultados.

### 15.2 Nested LOSO

Nested LOSO agrega una capa interna de seleccion de modelo:

```text
Outer LOSO: sujeto completamente fuera para evaluacion final
Inner LOSO: elegir modelo usando solo los sujetos de entrenamiento
```

Esto es metodologicamente riguroso, pero con 11 sujetos fue inestable. La
seleccion anidada produjo un MAE global aproximado de 0.750, peor que la mejor
hipotesis two-stage predefinida.

Interpretacion: nested LOSO ya esta implementado como auditoria, pero todavia no
debe usarse como autoridad final de seleccion con tan pocos sujetos.

### 15.3 Clasificacion high vs not-high

Tambien se probo clasificacion binaria:

```text
high = SCEM >= 4.0
not_high = SCEM < 4.0
```

Resultados:

| Modelo | Balanced accuracy | Macro F1 | Accuracy |
|---|---:|---:|---:|
| logistic sin calibracion | 0.379 | 0.363 | 0.383 |
| logistic con calibracion | 0.364 | 0.341 | 0.347 |

No se recomienda usar esta salida todavia. La razon principal es desbalance de
etiquetas.

### 15.4 Learning to rank

Learning to rank intenta aprender orden relativo en lugar de valor absoluto. En
este dataset mejoro MAE contra algunos modelos absolutos, pero Spearman siguio
negativo. Por ahora no supera a la arquitectura two-stage.

### 15.5 Multi-output learning

Multi-output intenta predecir por separado los cuatro items SCEM:

```text
task_engagement
effort
persistence
flow
```

Despues promedia las cuatro predicciones. Esta estrategia no mejoro el resultado
actual, probablemente porque hay pocos sujetos y pocas etiquetas por dimension.

## 16. XAI: explicabilidad del modelo

XAI significa Explainable Artificial Intelligence. Su objetivo es entender que
variables influyen en las predicciones.

En modelos XGBoost se uso SHAP. SHAP estima cuanto contribuye cada feature a
mover una prediccion hacia arriba o hacia abajo.

Conceptos clave:

- `mean_abs_shap`: importancia global promedio de una feature.
- `shap_value`: contribucion local de una feature en una prediccion especifica.
- SHAP no prueba causalidad; solo explica el comportamiento del modelo.

Ejemplos de factores importantes en XGBoost robusto:

- `eeg_channel_3_rel_gamma_low`;
- `eeg_channel_1_rel_beta`;
- `eeg_channel_4_rel_theta`;
- `ppg_median_hr_mean`;
- `ppg_iqr_pnn50`;
- `eeg_channel_2_log_beta_alpha`.

Como XGBoost no fue el mejor modelo final, estas explicaciones deben leerse como
exploratorias. Para el modelo Bayesian Ridge calibrado, la explicabilidad es mas
lineal: los coeficientes del modelo indican que features seleccionadas empujan
la prediccion relativa hacia arriba o hacia abajo.

## 17. Integracion al pipeline realtime

El monitoreo en tiempo real esta integrado en `muse_ml.realtime` y se activa
desde el EdgeAgent con:

```powershell
python -m muse_web.edge_agent `
  --cloud-url "wss://musepipeline.onrender.com/ws/agent/lab-windows-01" `
  --agent-id "lab-windows-01" `
  --agent-token "clave-agente-01" `
  --max-devices 1 `
  --sessions-root "$env:USERPROFILE\MuseResearch\sessions" `
  --enable-cognitive-cloud `
  --cognitive-model "ml_output\experiments\stage1_relative_scem_stage2_calibrator.joblib" `
  --cognitive-window-seconds 60 `
  --cognitive-update-seconds 30
```

El argumento `--enable-cognitive-cloud` es obligatorio para publicar resultados
cognitivos a la GUI cloud. Esto se hizo por seguridad: las predicciones
cognitivas son datos derivados sensibles.

## 18. Logica realtime implementada

En tiempo real, el sistema:

1. Lee la base SQLite activa.
2. Identifica el operador.
3. Toma la ventana reciente de EEG/PPG.
4. Calcula features con el mismo extractor usado en entrenamiento.
5. Busca el baseline fisiologico de `paso_1`.
6. Resta el baseline a las features actuales.
7. Carga el modelo stage 1.
8. Predice SCEM relativo.
9. Busca la primera respuesta SCEM del operador.
10. Reconstruye SCEM absoluto.
11. Suaviza usando las ultimas 3 predicciones.
12. Publica el estado cognitivo a la GUI cloud.

Si falta baseline de paso 1, reporta:

```text
waiting_for_paso_1_baseline
```

Si falta primera respuesta SCEM, reporta:

```text
waiting_for_scem_calibration
```

Esto evita producir un resultado aparentemente preciso pero metodologicamente
invalido.

## 19. Salida mostrada en la GUI

La GUI cloud muestra:

- operador;
- score SCEM estimado;
- nivel bajo, medio o alto;
- confianza basada en muestras disponibles;
- cantidad de muestras EEG/PPG;
- metodo usado;
- SCEM relativo;
- ancla de calibracion;
- score sin suavizar;
- suavizado aplicado;
- factores explicativos.

El score final mostrado es el score suavizado.

## 20. Seguridad y privacidad

Por defecto, EEG y PPG crudos permanecen en la computadora local. La nube recibe
estados operativos y, solo si se activa explicitamente, recibe predicciones
cognitivas derivadas.

La decision de hacer opt-in el monitoreo cognitivo cloud es importante porque
aunque no se envien senales crudas, el engagement estimado sigue siendo
informacion sensible sobre el participante.

## 21. Comandos principales

### 21.1 Crear dataset ML

```powershell
python -m muse_ml.dataset `
  --input-dir subject_data `
  --output-dir ml_output `
  --window-seconds 60 `
  --stride-seconds 30 `
  --label-half-life-seconds 180
```

### 21.2 Entrenar modelos base

```powershell
python -m muse_ml.train `
  --input-dir ml_output `
  --output-dir ml_output\models `
  --feature-set robust `
  --min-feature-coverage 0.6
```

### 21.3 Correr experimentos completos

```powershell
python -m muse_ml.experiments `
  --input-dir ml_output `
  --output-dir ml_output\experiments `
  --min-feature-coverage 0.6
```

### 21.4 Consultar resultados

```powershell
Get-Content ml_output\experiments\experiment_summary.json
```

```powershell
Import-Csv ml_output\experiments\experiment_results.csv |
  Select-Object -First 15 |
  Format-Table feature_set,target_type,model,feature_k,smoothing,calibration,MAE,RMSE,R2,Spearman,beats_baseline -AutoSize
```

```powershell
Import-Csv ml_output\models\xgboost_shap_global.csv |
  Select-Object -First 20 |
  Format-Table feature_name,mean_abs_shap,mean_shap -AutoSize
```

### 21.4.1 Consultar comparacion EEG-only vs PPG-only

```powershell
Import-Csv ml_output\experiments\experiment_results.csv |
  Where-Object {
    $_.target_type -eq "relative_scem" -and
    $_.model -eq "bayesian_ridge" -and
    $_.feature_k -eq "20" -and
    $_.smoothing -eq "rolling3" -and
    $_.calibration -eq "initial_subject" -and
    $_.physiological_baseline -eq "paso_1"
  } |
  Select-Object feature_set,MAE,RMSE,R2,Spearman,balanced_accuracy,macro_f1,beats_baseline |
  Format-Table -AutoSize
```

### 21.5 Correr realtime con modelo calibrado

```powershell
python -m muse_web.edge_agent `
  --cloud-url "wss://musepipeline.onrender.com/ws/agent/lab-windows-01" `
  --agent-id "lab-windows-01" `
  --agent-token "clave-agente-01" `
  --max-devices 1 `
  --sessions-root "$env:USERPROFILE\MuseResearch\sessions" `
  --enable-cognitive-cloud `
  --cognitive-model "ml_output\experiments\stage1_relative_scem_stage2_calibrator.joblib" `
  --cognitive-window-seconds 60 `
  --cognitive-update-seconds 30
```

## 22. Limitaciones actuales

1. El numero de sujetos sigue siendo bajo.
2. La clase de engagement bajo esta subrepresentada.
3. El paso 1 es baseline pasivo de tarea, no reposo fisiologico.
4. El mejor modelo requiere calibracion inicial por sujeto.
5. El R2 todavia no es claramente positivo.
6. Nested LOSO es metodologicamente correcto, pero inestable con pocos sujetos.
7. La prediccion realtime debe considerarse experimental.

## 23. Recomendaciones metodologicas siguientes

1. Mantener `paso_1` como baseline fisiologico formal en el protocolo.
2. Asegurar que todos los sujetos respondan SCEM temprano para calibracion.
3. Recolectar mas ejemplos de engagement bajo y medio.
4. Reportar siempre baseline_train_mean junto al modelo.
5. Mantener LOSO como metrica principal.
6. Usar el modelo two-stage como prototipo realtime actual.
7. No reportar el clasificador high/not-high como modelo final todavia.
8. Cuando aumenten sujetos, repetir nested LOSO para seleccion formal.

## 24. Conclusion

El pipeline actual ya permite entrenar, validar, explicar y desplegar un modelo
experimental de engagement cognitivo. La implementacion inicial basada en SCEM
absoluto no superaba al baseline. La arquitectura calibrada two-stage si logro
superarlo usando el dataset actual:

```text
MAE baseline: 0.607
MAE two-stage: 0.561
Spearman baseline: -0.716
Spearman two-stage: 0.525
```

Esto representa una mejora metodologicamente significativa, especialmente porque
el modelo paso de ordenar incorrectamente los estados a mostrar una asociacion
positiva con el SCEM real. Aun asi, el sistema debe describirse como prototipo
experimental hasta contar con mas sujetos y una distribucion mas balanceada de
engagement.
