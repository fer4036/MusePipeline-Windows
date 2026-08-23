# Reporte técnico integral del pipeline Muse Research

**Estado documentado:** agosto de 2026  
**Plataforma actual:** Ubuntu 22.04, ROS 2 Humble, BlueZ y Muse S Athena  
**Plataforma objetivo aprobada:** Raspberry Pi 5, Ubuntu 24.04 ARM64, ROS 2 Jazzy y SSD NVMe  
**Ubicación del proyecto:** `/home/fernanda/muse`  
**Alcance:** adquisición local simultánea de EEG, IMU y PPG desde varias diademas Muse

---

## 1. Propósito de este documento

Este reporte explica el pipeline completo sin asumir que la persona lectora conoce el
proyecto, electroencefalografía, Bluetooth Low Energy, ROS 2, bases de datos o
programación. Al mismo tiempo, conserva los detalles necesarios para que un perfil
técnico pueda entender, mantener, auditar y extender la implementación.

El documento responde cinco preguntas principales:

1. ¿Qué problema científico y operativo resuelve el sistema?
2. ¿Cómo se transportan los datos desde una diadema física hasta un archivo local?
3. ¿Qué cambió respecto de la arquitectura inicial y por qué?
4. ¿Qué hace exactamente cada nodo, tópico, servicio y componente web?
5. ¿Qué capacidades están terminadas y qué limitaciones siguen abiertas?

La descripción se basa en el código presente en el workspace en agosto de 2026. Distingue
explícitamente entre la versión local implementada, las correcciones comprobadas, los
problemas todavía abiertos y la arquitectura futura ya seleccionada. La migración a
Raspberry Pi 5 y ROS 2 Jazzy se documenta como trabajo futuro: todavía no forma parte de
la versión ejecutable descrita en las secciones de operación.

---

## 2. Resumen ejecutivo

Muse Research es una plataforma local de adquisición multimodal. Su función es conectar
una o varias diademas Muse S con firmware Athena, recibir sus señales en tiempo real,
identificar a cada diadema como un operador dentro de una sesión, supervisar la calidad
operativa del enlace y guardar únicamente los intervalos que el equipo de investigación
decida registrar.

El sistema actual adquiere tres familias de datos:

| Señal | Qué representa | Frecuencia nominal | Contenido actual |
|---|---|---:|---|
| EEG | Diferencias de potencial eléctrico medidas en el cuero cabelludo | 256 muestras/s | 4 canales: TP9, AF7, AF8 y TP10 |
| IMU | Movimiento de la cabeza | 52 muestras/s | Acelerómetro de 3 ejes y giroscopio de 3 ejes |
| PPG | Intensidad óptica reflejada por el tejido | 64 muestras/s | 16 canales ópticos crudos |

El sistema **no calcula todavía frecuencia cardiaca en BPM ni HRV**. El PPG contiene la
materia prima para hacerlo, pero el procesamiento fisiológico debe implementarse y
validarse por separado.

La arquitectura usa ROS 2 para separar responsabilidades:

- `auto_discovery` encuentra diademas y administra adaptadores Bluetooth.
- Un nodo `muse_operador_x` por diadema decodifica y publica sus señales.
- `central_database` se suscribe dinámicamente y persiste los datos.
- La aplicación web local prepara sesiones, inicia o detiene la grabación, muestra
  usuarios y métricas, y genera el archivo de entrega.

Los datos no se envían a Supabase ni a otro servicio en la nube. Durante la captura se
guardan en SQLite, porque una base transaccional es más segura frente a interrupciones
que escribir directamente un archivo de texto. Al finalizar se produce un
par de CSV por usuario: uno estrictamente Muse y otro completo con workshop y Likert.
Todos permanecen en la computadora local.

La versión estable actual sigue siendo **local-first** y se ejecuta en la computadora
principal. La siguiente etapa aprobada trasladará la adquisición a una Raspberry Pi 5
con SSD NVMe en cada laboratorio y publicará la interfaz mediante un servicio web online.
La Raspberry conservará BlueZ, ROS 2, SQLite y los CSV; los navegadores de participantes
e investigadores sólo necesitarán HTTPS. Esta separación evita depender de que las
computadoras del taller puedan comunicarse directamente entre sí dentro de la Wi-Fi.

---

## 3. Objetivos del proyecto

### 3.1 Objetivo principal

Proporcionar un medio reproducible y suficientemente robusto para recolectar señales
fisiológicas y de movimiento de varias personas durante experimentos de interacción
humano-robot, carga cognitiva u otras tareas experimentales.

### 3.2 Objetivos funcionales

- Conectar varias diademas simultáneamente.
- Evitar escribir manualmente una dirección MAC en cada prueba.
- Dedicar un adaptador Bluetooth a cada diadema conectada.
- Conservar la identidad `operador_a`, `operador_b`, etc. cuando una diadema se
  desconecta y reconecta dentro de la misma sesión.
- Detectar caídas de conexión y reintentar sin intervención manual.
- Transportar EEG, IMU y PPG con marcas de tiempo y tipos de mensaje explícitos.
- Permitir que las diademas se conecten antes de comenzar el experimento.
- Separar “estar transmitiendo” de “estar guardando”.
- Guardar sólo los intervalos seleccionados por la persona investigadora.
- Mantener los datos sensibles en almacenamiento local.
- Permitir supervisión sin exigir conocimientos de terminal o ROS 2.
- Conservar herramientas técnicas de diagnóstico para el equipo de desarrollo.

### 3.3 Uso científico previsto

El reporte inicial del proyecto plantea análisis posteriores como:

- extracción de potencia por bandas EEG y clasificación con Random Forest o SVM;
- clasificación de señal cruda mediante EEGNet;
- evaluación Leave-One-Subject-Out y aumento de datos;
- interpretabilidad mediante SHAP o Grad-CAM;
- fusión futura de EEG con métricas derivadas de PPG, como frecuencia cardiaca y HRV.

Es importante separar adquisición y análisis: **el pipeline actual recolecta y organiza
señales; no entrena modelos, no clasifica estados cognitivos y no calcula HRV**.

---

## 4. Conceptos científicos básicos

### 4.1 Qué es EEG

EEG significa electroencefalografía. Una diadema EEG utiliza electrodos para medir
diferencias de potencial muy pequeñas en la superficie de la cabeza. Estas variaciones
se expresan habitualmente en microvoltios.

La Muse Athena entrega cuatro canales EEG con el siguiente orden:

| Índice | Canal | Ubicación aproximada |
|---:|---|---|
| 1 | TP9 | Zona temporoparietal izquierda, cerca de la oreja |
| 2 | AF7 | Zona frontal izquierda |
| 3 | AF8 | Zona frontal derecha |
| 4 | TP10 | Zona temporoparietal derecha, cerca de la oreja |

Un “canal” no equivale directamente a una región cerebral aislada. La señal observada
combina actividad eléctrica, referencia, contacto del electrodo y artefactos. Movimiento,
parpadeo, actividad muscular, cabello y mal contacto pueden modificarla.

La frecuencia nominal de 256 Hz significa que la diadema produce aproximadamente 256
muestras por segundo por canal. No significa que la GUI o `ros2 topic hz` siempre deban
mostrar exactamente 256 mensajes por segundo: el protocolo puede entregar paquetes con
varias muestras y el sistema después publica cada muestra, por lo que planificación del
sistema operativo, lotes y carga introducen variación observable.

Este equipo y este software son instrumentos de investigación, no un sistema médico de
diagnóstico.

### 4.2 Qué es PPG

PPG significa fotopletismografía. La diadema emite luz y mide cuánta luz retorna. Los
cambios de volumen sanguíneo afectan esa señal, por lo que un procesamiento posterior
puede estimar pulsos, frecuencia cardiaca y métricas de variabilidad.

La Athena entrega 16 valores ópticos por muestra en unidades arbitrarias. El pipeline
los etiqueta `OPT0` a `OPT15` en el mensaje y `channel_1` a `channel_16` en SQLite.

El pipeline no puede llamar “ritmo cardiaco” a esos valores crudos. Para producir BPM o
HRV se requieren, al menos, selección de canales, filtrado, detección de picos, control
de artefactos, definición de ventanas y validación contra una referencia.

### 4.3 Qué es una IMU

IMU significa unidad de medición inercial. En este proyecto reúne:

- acelerómetro: aceleración lineal en los ejes X, Y y Z;
- giroscopio: velocidad angular alrededor de los ejes X, Y y Z.

La librería Athena entrega aceleración en múltiplos de gravedad y giroscopio en grados
por segundo. `muse_node` transforma:

- aceleración a metros por segundo al cuadrado multiplicando por `9.80665`;
- velocidad angular a radianes por segundo.

La diadema no entrega una orientación absoluta ya fusionada. Por esa razón, el mensaje
ROS `sensor_msgs/Imu` marca la orientación como no disponible.

### 4.4 Marca de tiempo y sincronización

Cada muestra lleva una marca de tiempo dividida en segundos y nanosegundos. Esto permite
ordenar las señales y relacionar EEG, IMU y PPG.

Athena incluye un reloj de dispositivo, pero durante la validación física se observó que
la combinación de firmware y decoder evaluada podía publicar un tick que no avanzaba.
El nodo ancla por ello cada lote al `CLOCK_REALTIME` de la computadora y reconstruye el
espaciado interno con la frecuencia nominal de EEG, IMU o PPG. Los intervalos del taller
y las respuestas de ground truth usan el mismo reloj local.

La existencia de marcas de tiempo no garantiza por sí sola una sincronización de grado
metrológico entre cuatro dispositivos independientes. Para experimentos que requieran
precisión submilisegundo debe medirse el error real entre diademas y documentar deriva,
latencia y reloj de referencia.

---

## 5. Conceptos tecnológicos básicos

### 5.1 Bluetooth Low Energy, BlueZ y `hci`

Bluetooth Low Energy, o BLE, es el medio inalámbrico entre la diadema y la computadora.
BlueZ es la implementación Bluetooth de Linux.

Linux identifica cada controlador Bluetooth con un nombre como:

- `hci0`: normalmente el Bluetooth integrado;
- `hci1`, `hci2`, etc.: adaptadores USB adicionales.

La arquitectura asigna un `hci` específico a cada diadema para disminuir interferencia
operativa entre conexiones y hacer explícito qué controlador es responsable de cada
stream.

GATT es el modelo mediante el que un dispositivo BLE expone servicios y características.
Athena publica datos por características propietarias de Interaxon. La aplicación se
suscribe a notificaciones GATT para recibir los paquetes.

### 5.2 Qué es ROS 2

ROS 2 es un middleware: un conjunto de herramientas para que procesos independientes se
encuentren e intercambien mensajes. En este proyecto no controla un robot directamente;
se utiliza como “sistema nervioso” del pipeline de adquisición.

Los términos fundamentales son:

- **Nodo:** programa con una responsabilidad concreta.
- **Tópico:** canal de mensajes continuo, similar a una estación de radio.
- **Publicador:** nodo que envía mensajes a un tópico.
- **Suscriptor:** nodo que recibe mensajes de un tópico.
- **Mensaje:** estructura tipada de los datos enviados.
- **Servicio:** operación de solicitud y respuesta; aquí se usa para activar o detener
  la grabación.
- **Parámetro:** configuración que recibe un nodo al iniciar.
- **Grafo ROS:** conjunto actual de nodos, tópicos y servicios.

Una ventaja esencial es el desacoplamiento: el nodo que lee Bluetooth no necesita saber
si los datos terminarán en SQLite, en una gráfica o en otra herramienta. Sólo publica
mensajes con un contrato conocido.

### 5.3 Proceso, hilo y cola

Un proceso es una instancia aislada de un programa. Cada diadema usa su propio proceso
`muse_node`, de modo que una falla de una diadema no comparte directamente su memoria
con las demás.

Dentro de ese proceso hay varios hilos. El enlace BLE se atiende en un hilo y ROS 2
publica desde su ejecutor. Entre ambos se usan colas seguras para hilos. Esta separación
evita publicar desde el contexto interno de la librería Bluetooth.

---

## 6. Evolución de la arquitectura

### 6.1 Punto de partida

En julio de 2026 el sistema funcionaba principalmente con una diadema Muse S Athena y
un solo controlador Bluetooth. El reporte original identificaba problemas para escalar:
desconexiones, falta de control explícito del adaptador, reconexión lenta, escritura
SQLite por muestra, PPG inactivo y mensajes poco estructurados.

BrainFlow se descartó porque la versión evaluada no entendía el protocolo Athena. Se
adoptó `muselsl` con soporte Athena.

### 6.2 Cambios principales y razón de cada uno

| Antes o problema inicial | Cambio actual | Razón |
|---|---|---|
| Un adaptador compartido | Pool configurable `hci0...hciN` y uno asignado por diadema | Reducir contención y hacer determinista el controlador BLE |
| MAC escrita manualmente | Detección por propiedades BLE | Evitar operación manual y direcciones fijas |
| Reconocimiento sólo por nombre | Nombre, UUID Interaxon/Muse, fabricante configurable u OUI conocido | Las Muse pueden anunciarse sin nombre legible |
| Caché BlueZ confundía dispositivos apagados | Sólo se clasifican direcciones observadas en el scan actual y se excluyen `[DEL]` | Evitar falsos positivos históricos |
| Conexiones simultáneas competían | Preparación y conexión mediante cola FIFO | BlueZ serializa varias operaciones de controlador |
| Pérdida GATT no cambiaba el estado | Callback inmediato más watchdog de 10 s | Detectar stream muerto aunque el proceso siga vivo |
| Reconexión podía crear identidades nuevas | La entrada MAC-operador vive durante toda la sesión | Reutilizar el operador tras reconexión |
| Publicación desde callback BLE | Colas por señal y publicación desde ROS 2 | Seguridad entre hilos y control de sobrecarga |
| Sin visibilidad de pérdidas | Contadores recibidos, publicados, descartados y en cola | Diagnóstico cuantitativo |
| JSON para muestras | Mensajes `EegSample`, `PpgSample` y `sensor_msgs/Imu` | Contratos tipados y timestamps estándar |
| IMU parcial | Acelerómetro y giroscopio unidos por timestamp | Conservar seis ejes en un mensaje estándar |
| PPG inactivo | Callback óptico, tópico y tabla de 16 canales | Habilitar análisis cardiaco futuro |
| `commit` por muestra | Lotes cada 100 ms o 1024 filas | Escalar a varias diademas sin miles de commits/s |
| Conectar implicaba grabar | Servicio ROS `set_recording` | Permitir preparación antes del inicio experimental |
| Sesión operada por terminal | GUI web local | Hacer el sistema accesible al equipo de investigación |
| Archivo global único | Directorio y SQLite por sesión | Separar pruebas y reducir mezcla accidental |
| Entrega para Excel | Exportación CSV UTF-8 rectangular | Formato solicitado, abierto y auditable |
| Sin batería visible | Lectura de telemetría Athena en métricas | Ayudar a prevenir pruebas con carga insuficiente |
| Tick Athena no avanzaba en prueba física | Reloj local por lote y marcas de filas EEG por sección | Mantener la alineación con eventos y ground truth |

### 6.3 Estado de las prioridades originales

| Mejora original | Estado actual | Observación |
|---|---|---|
| Adaptadores externos dedicados | Implementada | Backend Bleak fijado al `hci` asignado |
| Detección de desconexión | Implementada | Callback GATT y timeout de datos |
| Cola segura entre BLE y ROS | Implementada | Colas acotadas y métricas de descarte |
| Detección en caliente | Implementada con condición | Escanea si queda un adaptador libre y no hay conexión en curso |
| Mapeo persistente MAC-operador | Implementado sólo por sesión | No se conserva entre ejecuciones, por decisión operativa |
| Escritura por lotes | Implementada | 100 ms o 1024 filas acumuladas |
| PPG | Implementada como señal cruda | BPM y HRV siguen pendientes |
| Mensajes ROS tipados | Implementada | EEG/PPG propios e IMU estándar |
| Interfaz de operación | Implementada | FastAPI y frontend accesible en la LAN con claves por rol |
| Inicio/detención manual de registro | Implementada | No desconecta las diademas |

---

## 7. Arquitectura actual de extremo a extremo

```text
┌──────────────────────────────── HARDWARE ────────────────────────────────┐
│ Muse A       Muse B       Muse C       Muse D                           │
│ EEG/IMU/PPG  EEG/IMU/PPG  EEG/IMU/PPG  EEG/IMU/PPG                     │
└────┬────────────┬────────────┬────────────┬──────────────────────────────┘
     │ BLE        │ BLE        │ BLE        │ BLE
┌────▼────┐  ┌────▼────┐  ┌────▼────┐  ┌────▼────┐
│  hci1   │  │  hci2   │  │  hci3   │  │  hci4   │   BlueZ / Linux
└────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘
     └────────────┴──────┬─────┴─────────────┘
                         ▼
               ┌───────────────────┐
               │ /auto_discovery   │
               │ scan, identidad,  │
               │ pool y watchdog   │
               └─────────┬─────────┘
                         │ crea un proceso por diadema
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
 ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
 │muse_operador_a │ │muse_operador_b │ │muse_operador_x │
 │Athena + colas  │ │Athena + colas  │ │Athena + colas  │
 └───────┬────────┘ └───────┬────────┘ └───────┬────────┘
         │ tópicos EEG / IMU / PPG / status     │
         └──────────────────┬────────────────────┘
                            ▼
                 ┌─────────────────────┐
                 │ /central_database   │
                 │ suscripciones       │
                 │ control grabación   │
                 │ lotes + SQLite WAL  │
                 └──────────┬──────────┘
                            ▼
              raw.sqlite ─────────► CSV Muse / CSV completo por operador
                    ▲
                    │ controla y consulta
              ┌─────┴───────────────┐
              │ GUI LAN FastAPI     │
              │ 0.0.0.0:8765        │
              └─────────────────────┘
```

### 7.1 Capas del sistema

1. **Capa física:** diademas y adaptadores USB.
2. **Capa BLE:** BlueZ, `bluetoothctl`, Bleak y GATT Athena.
3. **Capa de adquisición:** un `muse_node` por diadema.
4. **Capa de comunicación:** tópicos y servicio ROS 2.
5. **Capa de persistencia:** `central_database` y SQLite.
6. **Capa de operación:** servidor FastAPI y página web en la red local.
7. **Capa de entrega:** metadatos, log, SQLite original y CSV exportado.

---

## 8. Organización del workspace

| Paquete o ruta | Responsabilidad |
|---|---|
| `muse_msgs` | Define los mensajes ROS propios para EEG y PPG |
| `muse_hrc` | Descubrimiento BLE, adaptación Athena, nodos de señal y base de datos |
| `muse_web` | Gestión de sesiones, API local, GUI y exportadores |
| `requirements_athena.txt` | Fija la revisión de `muselsl` usada para Athena |
| `muse_env` externo al workspace | Runtime Python que debe importar `rclpy` y `muselsl.athena` |
| `web_env` | Entorno de FastAPI/Uvicorn y dependencias web |
| `build`, `install`, `log` | Artefactos generados por `colcon` y pruebas ROS 2 |

El repositorio contiene además un exportador XLSX heredado. Está probado y conserva
compatibilidad, pero la GUI actual usa `csv_export.py` y entrega CSV.

### 8.1 Entorno y dependencias

| Componente | Referencia actual |
|---|---|
| Sistema operativo de desarrollo | Ubuntu 22.04 |
| Middleware | ROS 2 Humble |
| Python | Familia 3.10 en el entorno actual |
| Hardware de referencia original | Muse S Gen 3 / Athena; firmware reportado 3.1.15 |
| Decoder Athena | `muselsl` desde commit `042881f2ed698778acafec638cc879ef6a6e5eb4` |
| BLE Python | Bleak, usado por el backend de `muselsl` |
| Bluetooth Linux | BlueZ, `bluetoothctl` y `hciconfig` |
| Base local | SQLite 3, modo WAL |
| API | FastAPI `>=0.115,<1` |
| Servidor web | Uvicorn `>=0.30,<1` |
| Excel heredado | OpenPyXL `>=3.1,<4` |

El firmware 3.1.15 es una referencia del equipo con el que se inició el proyecto, no una
validación automática de todas las diademas. Antes de una campaña debe inventariarse la
versión real de cada unidad. Tampoco existe todavía un lock completo de dependencias
transitivas; `requirements_athena.txt` fija el decoder, mientras que la GUI usa rangos.

---

## 9. Arranque del sistema

### 9.1 Archivo launch

`muse_system.launch.py` declara cinco argumentos:

| Argumento | Valor por defecto | Función |
|---|---|---|
| `hci_devices` | `hci0,hci1,hci2,hci3` | Pool ordenado de controladores permitidos |
| `muse_manufacturer_ids` | vacío | IDs adicionales para reconocer dispositivos |
| `muse_python` | `auto` | Intérprete usado para los nodos Athena |
| `database_path` | `~/muse_telemetry.db` | SQLite de salida |
| `recording_enabled` | `true` | Indica si la persistencia inicia inmediatamente |

El launch crea dos nodos base:

- ejecutable `database_node`, renombrado `/central_database`;
- ejecutable `discovery_node`, renombrado `/auto_discovery`.

Los nodos de cada diadema se crean dinámicamente después del descubrimiento.

### 9.2 Diferencia entre terminal y GUI

Si se lanza directamente con `ros2 launch`, `recording_enabled` vale `true`: se guarda
desde que aparecen los tópicos.

La GUI siempre lanza el mismo sistema con:

```text
recording_enabled:=false
```

Por eso puede conectar y verificar las diademas antes de grabar. Esta diferencia es
intencional para no romper el flujo histórico por terminal.

---

## 10. Nodo de descubrimiento: `/auto_discovery`

### 10.1 Responsabilidad

Es el coordinador de hardware. No decodifica EEG. Sus funciones son:

- validar qué adaptadores configurados existen;
- escanear anuncios BLE;
- distinguir Muse de dispositivos ajenos;
- asignar `operador_x` y un adaptador;
- preparar la conexión;
- crear y terminar procesos `muse_node`;
- serializar intentos de conexión;
- supervisar estados y reconexiones;
- reenviar métricas al log de la sesión.

### 10.2 Validación de adaptadores

Ejecuta `hciconfig -a`, extrae los nombres `hciN` y conserva únicamente la intersección
con `hci_devices`. Informa adaptadores configurados pero ausentes. Si no puede ejecutar
`hciconfig`, utiliza la configuración declarada como fallback; si no encuentra ninguno,
no lanza nodos de diadema.

Los adaptadores libres se almacenan en una cola. El primero libre se asigna a la
siguiente diadema; al terminar un proceso, vuelve al pool.

### 10.3 Scan BLE

- Primer scan: inmediato al iniciar.
- Periodicidad posterior: cada 15 segundos.
- Duración solicitada a `bluetoothctl`: 12 segundos.
- Timeout total del subproceso: 18 segundos.

Un scan se aplaza cuando:

- otro scan sigue activo;
- una diadema está en proceso de conexión;
- hay dispositivos esperando en la cola;
- no queda un adaptador libre.

Esto significa que puede detectar nuevas diademas mientras otras transmiten siempre que
exista capacidad libre. Si todos los adaptadores están ocupados, no intenta admitir una
diadema adicional.

### 10.4 Identificación automática por propiedades

El sistema no acepta cualquier dispositivo encontrado. Consulta `bluetoothctl info` y
considera una Muse si se cumple al menos una regla positiva:

1. nombre o alias contiene la palabra Muse;
2. anuncia UUID `FE8D`, asignado a Interaxon, o el namespace propietario `273e`;
3. anuncia un ID de fabricante incluido en `muse_manufacturer_ids`;
4. su dirección empieza con el OUI conocido `00:55:DA`.

Las direcciones se normalizan a minúsculas. Los dispositivos removidos durante el scan
se excluyen. Esto evita que la caché persistente de BlueZ haga aparecer una diadema
apagada.

### 10.5 Preparación BLE

Para cada Muse nueva se ejecuta temporalmente:

1. `bluetoothctl connect <MAC>`;
2. comprobación de respuesta exitosa;
3. `bluetoothctl disconnect <MAC>`.

No se hace pairing permanente porque Athena no necesita un bond. La conexión temporal
prepara el enlace y después lo libera para Bleak. Si esta preparación falla, el sistema
registra una advertencia pero permite que Athena intente conectar directamente.

### 10.6 Identidad de operador

Cada MAC nueva recibe secuencialmente `operador_a`, `operador_b`, `operador_c`, etc. La
entrada queda en memoria durante la ejecución completa del pipeline. Si la misma MAC se
desconecta y vuelve, conserva su operador.

El mapeo **no se conserva entre sesiones ni reinicios**. En una sesión futura, el orden
de encendido/detección puede producir otra asignación. Esta decisión evita convertir un
identificador físico permanente en identidad longitudinal, pero obliga a documentar la
correspondencia participante-operador en cada protocolo.

### 10.7 Cola y máquina de estados

Los estados internos son:

```text
SCANNING → CONNECTING → STREAMING
                 │           │
                 └── LOST ◄──┘
                       │
                       └── reintento → CONNECTING
```

Sólo una diadema establece conexión a la vez. La cola normal es FIFO. Los dispositivos
perdidos cuyo tiempo de espera terminó tienen prioridad, ordenados por `next_retry`.

El watchdog se ejecuta cada 3 segundos. Si una conexión no llega a streaming en 30
segundos, termina el proceso, libera el adaptador y reencola la diadema.

La espera de reintento es lineal: `5 s × número de intento`, limitada a 60 segundos.

### 10.8 Proceso hijo y logs

El nodo lanza:

```text
<python-athena> -m muse_hrc.muse_node --ros-args \
  -p operador_id:=operador_a \
  -p mac_address:=... \
  -p hci_device:=hci1
```

La salida propia del proceso se escribe en `/tmp/muse_operador_a.log`. Los estados que
publica por ROS se vuelven a registrar en el log principal como líneas
`MUSE_STATUS_JSON=...`, que la GUI puede interpretar sin depender de texto humano.

---

## 11. Adaptación de `muselsl` y protocolo Athena

### 11.1 Por qué existe `AdapterAthena`

La clase Athena original necesita una adaptación para fijar explícitamente el adaptador
BlueZ. `AdapterAthena` crea un backend Bleak que pasa `adapter=hciN` tanto al escáner
como al `BleakClient`.

Si una dirección BLE cambia y existe un nombre conocido, el backend puede escanear otra
vez sobre el mismo controlador y refrescar la dirección.

### 11.2 Secuencia de conexión

1. Crear backend Bleak dedicado.
2. Conectar con timeout de 30 segundos.
3. Verificar que el dispositivo expone la característica de datos Athena.
4. Suscribirse a control y datos.
5. Ejecutar la secuencia de inicialización del preset Athena.
6. Iniciar streaming.

Si falta la característica Athena, el sistema desconecta y reporta que podría no ser
una Muse S Athena compatible.

### 11.3 Reloj local de adquisición

Una prueba física detectó más de cien mil muestras EEG cuyos timestamps ocupaban solo
unos milisegundos, aunque la adquisición había durado varios minutos. Para impedir que
ese tick defectuoso rompa la alineación con el taller, `HostSampleClock` asigna a cada
lote el tiempo de recepción local y conserva el espaciado nominal entre sus muestras.
También impide que el tiempo retroceda entre lotes.

La validación de que un operador produjo EEG durante una sección usa adicionalmente
marcas de avance de la clave primaria de `eeg_logs` al inicio y al final. Así, el envío
del cuestionario no depende de un único mecanismo temporal.

### 11.4 Selección del runtime Python

`auto_discovery` no supone que el Python del launch tiene `muselsl`. Revisa, en orden:

1. intérprete indicado explícitamente por `muse_python`;
2. `VIRTUAL_ENV` activo;
3. `muse_env/bin/python` asociado al workspace;
4. intérprete actual;
5. `python3` encontrado en `PATH`.

Cada candidato debe importar simultáneamente `rclpy` y `muselsl.athena`. Esto evita
lanzar un proceso que sólo tiene una de las dos dependencias.

---

## 12. Nodo por diadema: `/muse_operador_x`

### 12.1 Responsabilidad

Cada diadema tiene un nodo y un proceso independientes. El nodo:

- establece y mantiene el enlace Athena;
- recibe callbacks EEG, acelerómetro, giroscopio, óptica y batería;
- transforma unidades;
- crea mensajes ROS con timestamp;
- publica las tres señales y el estado;
- mide tasas y pérdidas;
- detecta inactividad y reconecta.

### 12.2 Publicadores

Para `operador_a`, se crean:

| Tópico | Tipo | Profundidad de cola ROS |
|---|---|---:|
| `/operador_a/eeg` | `muse_msgs/msg/EegSample` | 1000 |
| `/operador_a/imu` | `sensor_msgs/msg/Imu` | 1000 |
| `/operador_a/ppg` | `muse_msgs/msg/PpgSample` | 1000 |
| `/operador_a/status` | `std_msgs/msg/String` con JSON | 20 |

Al proporcionar sólo una profundidad, ROS 2 construye un perfil QoS keep-last con esa
capacidad. Los tópicos existen mientras el proceso existe; su existencia no demuestra
que la diadema siga enviando datos. El estado `streaming` y las tasas son las pruebas de
flujo real.

### 12.3 Mensaje EEG

```text
std_msgs/Header header
string operator_id
uint16 sampling_rate_hz
float32[4] data
```

- `header.stamp`: tiempo de la muestra;
- `header.frame_id`: `muse_operador_a/eeg`;
- `operator_id`: `operador_a`;
- `sampling_rate_hz`: 256;
- `data`: TP9, AF7, AF8, TP10, en microvoltios.

Cada muestra del lote Athena se convierte en un mensaje individual.

### 12.4 Mensaje IMU

Se usa `sensor_msgs/msg/Imu`, un tipo estándar. Contiene:

- `angular_velocity`: X, Y, Z en rad/s;
- `linear_acceleration`: X, Y, Z en m/s²;
- `orientation`: no disponible;
- timestamp y `frame_id` `muse_operador_a/imu`.

Athena entrega acelerómetro y giroscopio mediante callbacks separados. El nodo conserva
temporalmente el último lote de aceleración y lo asocia al giroscopio si sus timestamps
difieren menos de 20 ms. Si no encuentra correspondencia, marca la aceleración como no
confiable mediante la covarianza estándar del mensaje.

### 12.5 Mensaje PPG

```text
std_msgs/Header header
string operator_id
uint16 sampling_rate_hz
float32[16] data
```

- `sampling_rate_hz`: 64;
- `data`: 16 canales ópticos crudos;
- unidades: arbitrarias.

### 12.6 Colas internas y control de sobrecarga

La librería BLE y ROS 2 no publican desde el mismo hilo. Existen:

- cola EEG: máximo 5000 mensajes;
- cola IMU: máximo 5000 mensajes;
- cola PPG: máximo 5000 mensajes;
- cola de estado: máximo 100 mensajes.

Un timer de 1 ms vacía como máximo 512 elementos por señal en cada ciclo. Si una cola
sensorial está llena, la muestra nueva se descarta y aumenta el contador `dropped`.
Si la cola de estado está llena, se elimina el estado más antiguo para dejar entrar el
nuevo.

Este diseño hace visible la sobrecarga en lugar de permitir crecimiento ilimitado de
memoria.

### 12.7 Supervisión de conexión

- Bleak llama inmediatamente `_on_disconnect` cuando detecta pérdida GATT.
- Un timer cada segundo comprueba inactividad.
- Diez segundos sin EEG durante streaming se consideran pérdida de datos.
- El loop BLE se bombea cada 100 ms para procesar notificaciones síncronas.
- Se envía keepalive Athena cada 20 segundos.
- Un solo hilo de conexión puede estar activo por nodo.

El nodo intenta reconectar sin cambiar su operador ni adaptador. Si no logra estabilizarse
en 30 segundos, el coordinador puede terminarlo y relanzarlo.

### 12.8 Métricas y batería

Cada 10 segundos se publica un estado con:

- Hz recibidos de EEG, IMU y PPG;
- Hz publicados;
- acumulados recibidos y publicados;
- descartes por señal;
- profundidad actual de cada cola;
- estado streaming y momento de conexión;
- porcentaje de batería, si Athena ya lo reportó.

La batería se obtiene actualmente del atributo interno `_battery` de la clase Athena y
se limita visualmente a 100 %. Al depender de un atributo privado de una dependencia,
una actualización de `muselsl` podría requerir adaptar esta lectura.

---

## 13. Grafo ROS 2 actual

Con dos usuarios conectados, el grafo conceptual es:

```text
/auto_discovery
/central_database
/muse_operador_a
/muse_operador_b
```

Y los tópicos relevantes son:

```text
/operador_a/eeg      /operador_b/eeg
/operador_a/imu      /operador_b/imu
/operador_a/ppg      /operador_b/ppg
/operador_a/status   /operador_b/status
/parameter_events
/rosout
```

Para cuatro diademas se agregan los grupos `operador_c` y `operador_d`.

### 13.1 Servicio de control

`central_database` crea el servicio privado `~/set_recording`, que al aplicar el nombre
del nodo se resuelve como:

```text
/central_database/set_recording
```

Tipo:

```text
std_srvs/srv/SetBool
```

Una solicitud `data: true` activa persistencia. `data: false` vacía los lotes pendientes,
cierra el intervalo y pausa nuevas inserciones. La transmisión ROS continúa.

### 13.2 Qué significa `ros2 topic hz`

La herramienta mide la frecuencia de llegada de mensajes durante una ventana. Reporta:

- `average rate`: mensajes promedio por segundo;
- `min`: menor intervalo observado entre mensajes;
- `max`: mayor intervalo observado;
- `std dev`: variabilidad de los intervalos;
- `window`: cantidad de intervalos incluidos.

Un `min` de cero puede aparecer por ráfagas de mensajes publicadas casi juntas. Una tasa
menor a la nominal no prueba por sí sola pérdida en el sensor: debe compararse con los
contadores internos, descartes, carga de CPU y filas persistidas.

---

## 14. Nodo de base de datos: `/central_database`

### 14.1 Descubrimiento dinámico de operadores

Cada 3 segundos consulta el grafo. Cuando aparece un tópico que termina en `/eeg`,
extrae el nombre de operador y crea tres suscripciones: EEG, IMU y PPG. Cada operador se
registra una sola vez en `suscripciones_activas`.

Esto permite que el nodo de base de datos arranque antes que las diademas y se adapte a
usuarios que aparecen después.

### 14.2 Puerta de grabación

Los callbacks comprueban `recording_enabled` antes de añadir datos a memoria:

- si es `false`, el mensaje circula por ROS pero se ignora para persistencia;
- si es `true`, se incorpora al lote correspondiente.

EEG, IMU y PPG comparten el mismo estado de grabación para evitar que una modalidad se
active accidentalmente sin las otras.

### 14.3 SQLite y modo WAL

SQLite guarda todas las tablas en un archivo local. Se activa WAL, Write-Ahead Logging.
En este modo los cambios se escriben primero en un log de transacciones y después se
consolidan. Entre sus ventajas:

- atomicidad de transacciones;
- mejor convivencia entre escritura y preview de lectura;
- recuperación más segura ante cierre inesperado que un TXT escrito continuamente.

SQLite sigue siendo el original de adquisición. Cada CSV por operador es una representación de
intercambio, no el motor de base de datos activo.

### 14.4 Escritura por lotes

Existen tres listas pendientes. Se hace commit cuando ocurre primero:

- timer de 0.1 segundos;
- suma de pendientes igual o mayor a 1024 filas.

Cada tabla usa `executemany` dentro de la misma transacción. Si SQLite devuelve error:

1. se ejecuta rollback;
2. las filas fallidas regresan al frente de las listas;
3. se registra el error.

Al destruir el nodo se hace un último commit y se cierra la conexión.

### 14.5 Esquema de tablas

#### `eeg_logs`

| Columna | Tipo | Contenido |
|---|---|---|
| `id` | INTEGER | Secuencia autoincremental |
| `operador` | TEXT | `operador_a`, etc. |
| `timestamp` | REAL | Segundos Unix con fracción |
| `channel_1` | REAL | TP9 |
| `channel_2` | REAL | AF7 |
| `channel_3` | REAL | AF8 |
| `channel_4` | REAL | TP10 |
| `channel_5` | REAL | Campo heredado; se rellena con 0 |

El mensaje moderno contiene cuatro canales. La quinta columna permanece por
compatibilidad con bases anteriores y no debe interpretarse como un electrodo real.

#### `imu_logs`

| Columna | Tipo | Contenido |
|---|---|---|
| `id` | INTEGER | Secuencia |
| `operador` | TEXT | Identidad de sesión |
| `timestamp` | REAL | Tiempo de muestra |
| `gyro_x/y/z` | REAL | Velocidad angular en rad/s |
| `accel_x/y/z` | REAL | Aceleración en m/s² |

La inicialización migra esquemas antiguos: si faltan las columnas de aceleración, las
agrega mediante `ALTER TABLE`.

#### `ppg_logs`

Contiene `id`, `operador`, `timestamp` y `channel_1` a `channel_16`. Cada fila representa
una muestra óptica completa.

#### `recording_periods`

| Columna | Contenido |
|---|---|
| `id` | Número de intervalo |
| `started_at` | Tiempo de inicio según la computadora |
| `ended_at` | Tiempo de detención; vacío mientras está abierto |

### 14.6 Métricas de persistencia

Cada 10 segundos el nodo informa acumulados guardados por modalidad, estado de grabación
y filas todavía pendientes. Estos acumulados corresponden a la ejecución actual, no a
todo el contenido histórico de la base.

---

## 15. Aplicación web en red local

### 15.1 Objetivo

La GUI traduce operaciones de terminal a controles comprensibles. No sustituye ROS 2;
lo inicia, consulta y controla mediante procesos y servicios locales.

### 15.2 Servidor

- Framework: FastAPI.
- Servidor: Uvicorn.
- Dirección de escucha: `0.0.0.0` (todas las interfaces de la computadora central).
- Puerto por defecto: `8765`.
- Sin Swagger, ReDoc ni OpenAPI públicos.
- Directorio estático: HTML, CSS y JavaScript incluidos en `muse_web`.

Al iniciar, el servidor imprime un enlace de investigador y otro de participante con
claves independientes. Otros dispositivos de la misma Wi-Fi/LAN pueden acceder; el
servicio no debe exponerse mediante port forwarding ni publicarse en Internet.

#### 15.2.1 Direccionamiento, mDNS y coexistencia con el robot uFactory

La GUI ofrece el alias `muse-research.local` y una dirección IP como respaldo. Un
publicador Avahi revisa periódicamente las direcciones IPv4 de la computadora anfitriona
y vuelve a anunciar el alias cuando cambia la dirección seleccionada. Las credenciales
persisten entre reinicios en un archivo privado; al abrir un enlace válido, el servidor
crea una cookie `HttpOnly` y el frontend conserva un respaldo limitado a la pestaña para
navegadores que no aceptan correctamente cookies sobre una IP local.

Durante los talleres, cada computadora participante usa dos redes con propósitos
distintos:

```text
Wi-Fi institucional:       Internet y acceso a la GUI
Ethernet 192.168.0.XX/24:  robot uFactory
o Ethernet 192.168.1.XX/24
```

En la interfaz Ethernet sólo se modifica la dirección IPv4 y la máscara
`255.255.255.0`; no se añade gateway ni DNS. Por tanto, esa configuración no debería
reemplazar la ruta predeterminada de Internet. Sí crea una ruta conectada para todo el
prefijo `192.168.0.0/24` o `192.168.1.0/24`: cualquier destino de ese mismo prefijo se
envía por el cable del robot, no por Wi-Fi.

La implementación local actual prefiere por defecto una dirección propia dentro de
`192.168.0.0/24` al construir los enlaces. Esa decisión sólo es correcta si todas las
computadoras comparten físicamente ese mismo segmento Ethernet. Si cada equipo está
conectado únicamente a su propio robot, publicar `muse-research.local` con una dirección
`192.168.0.x` dirige el tráfico hacia el robot y hace inalcanzable el servidor. En ese
escenario debe publicarse la dirección Wi-Fi real, como la observada
`10.22.226.164/20`, o configurar explícitamente `MUSE_WEB_LAN_IP` con la interfaz que sí
comparten los clientes.

Incluso utilizando la dirección Wi-Fi correcta, una red institucional puede impedir la
comunicación directa entre clientes mediante aislamiento de estaciones, VLAN o bloqueo
de multicast mDNS. Compartir el mismo SSID no demuestra conectividad lateral. Esta
dependencia de la infraestructura del laboratorio es una limitación abierta de la
versión LAN y una motivación principal para la arquitectura online futura.

### 15.3 Gestor de sesión

`SessionManager` permite una sola sesión activa. Al preparar:

1. valida el formato de adaptadores con una expresión regular estricta;
2. normaliza código y experimento para formar un nombre seguro;
3. crea un directorio privado;
4. escribe `metadata.json` de forma atómica;
5. lanza `ros2 launch` en un grupo de procesos nuevo;
6. redirige stdout y stderr a `pipeline.log`;
7. deja la base en espera, sin grabar.

Los estados de sesión incluyen `preparing`, `ready`, `recording`, `completed`,
`pipeline_exited` y `launch_error`.

Puede haber varios intervalos start/stop dentro de una sesión. Cada intervalo se guarda
tanto en metadatos JSON como en `recording_periods` de SQLite.

### 15.4 Funciones visibles

- Preparar pipeline y conectar.
- Comenzar registro.
- Detener registro sin desconectar.
- Finalizar sesión y crear CSV.
- Ver usuarios, estado, adaptador, tiempo conectado y batería.
- Ver tasas publicadas de EEG, IMU y PPG.
- Medir un tópico durante 4 segundos con `ros2 topic hz --window 200`.
- Consultar `ros2 node list` y `ros2 topic list -t`.
- Ver las últimas 60 líneas del log.
- Previsualizar conteos y hasta 20 filas recientes por tabla; la GUI pide 5.
- Listar sesiones históricas y descargar o regenerar CSV.

### 15.5 API local

| Método y ruta | Función |
|---|---|
| `GET /api/status` | Pipeline, grabación, sesión, operadores y tail |
| `GET /api/sessions` | Lista de sesiones locales |
| `POST /api/session/start` | Preparar nueva sesión |
| `POST /api/session/stop` | Finalizar, detener procesos y exportar |
| `POST /api/recording/start` | Activar persistencia |
| `POST /api/recording/stop` | Pausar persistencia |
| `GET /api/database/preview` | Preview fijo y de sólo lectura |
| `POST /api/topic/hz` | Medición temporal de un tópico validado |
| `GET /api/ros/graph` | Nodos y tópicos tipados |
| `POST /api/session/export` | Regenerar CSV |
| `GET /api/session/{name}/csv/{operator}/{profile}` | Descargar perfil `muse` o `complete` |

### 15.6 Lectura del estado de usuarios

La GUI no se suscribe directamente a ROS. Lee hasta los últimos 2 MB de `pipeline.log`,
busca líneas `MUSE_STATUS_JSON` y reconstruye el estado más reciente por operador.

Las métricas periódicas repiten `streaming` y `connected_since`, por lo que la GUI puede
recuperar estado aunque la primera línea de conexión ya no esté en esa ventana.

### 15.7 Preview de base de datos

El backend abre SQLite con `mode=ro`, limita el tiempo de espera a 2 segundos y consulta
únicamente una lista fija de tablas. No acepta SQL libre desde el navegador. Devuelve:

- número total de filas;
- últimas filas por `id`;
- nombres de columnas y valores.

### 15.8 Terminación segura

Al finalizar, el gestor intenta:

1. detener la grabación si seguía activa;
2. enviar `SIGINT` al grupo y esperar 15 s;
3. si no termina, enviar `SIGTERM` y esperar 5 s;
4. como último recurso, enviar `SIGKILL` y esperar 3 s;
5. actualizar metadatos;
6. exportar CSV.

Al cerrar el servidor web, `shutdown()` finaliza y exporta una sesión que siga activa.

---

## 16. Flujo operativo de una sesión

### 16.1 Preparación sin grabación

```text
Usuario pulsa PREPARAR
        ↓
GUI crea directorio y lanza ROS 2 con recording=false
        ↓
auto_discovery valida hci y escanea BLE
        ↓
identifica Muse, asigna operador y adaptador
        ↓
muse_node conecta, decodifica y publica
        ↓
central_database crea suscripciones
        ↓
mensajes llegan, pero los callbacks no los insertan
        ↓
GUI muestra streaming, Hz, batería y tiempo conectado
```

Esta fase permite colocar correctamente las diademas y verificar que todos los usuarios
están listos sin contaminar el conjunto experimental.

### 16.2 Grabación

```text
Usuario pulsa COMENZAR
        ↓
POST /api/recording/start
        ↓
ros2 service call /central_database/set_recording true
        ↓
se abre recording_period
        ↓
EEG/IMU/PPG entran a lotes
        ↓
commit SQLite cada 100 ms o 1024 filas
```

### 16.3 Pausa

```text
Usuario pulsa DETENER REGISTRO
        ↓
servicio false
        ↓
se desactiva la puerta, se vacían lotes y se cierra el intervalo
        ↓
las diademas continúan conectadas y publicando
```

### 16.4 Finalización

```text
Usuario pulsa FINALIZAR
        ↓
se detiene grabación si era necesario
        ↓
se cierra el grupo ROS 2
        ↓
SQLite queda consolidado
        ↓
se genera un CSV temporal por operador
        ↓
os.replace publica cada CSV completo de forma atómica
```

---

## 17. Archivos producidos por sesión

Ruta:

```text
~/MuseResearch/sessions/<AAAAMMDD_HHMMSS_codigo_experimento>/
```

| Archivo | Uso |
|---|---|
| `raw.sqlite` | Fuente transaccional original |
| `raw.sqlite-wal` / `raw.sqlite-shm` | Archivos temporales mientras SQLite WAL está activo |
| `metadata.json` | Código seudónimo, experimento, notas, adaptadores, estados e intervalos |
| `pipeline.log` | Eventos ROS 2 y estados estructurados |
| `export_operador_x_muse.csv` | Sólo EEG, IMU y PPG del operador |
| `export_operador_x_completo.csv` | Señales, metadata, intervalos, workshop y Likert |

El directorio raíz y los directorios de sesión usan permisos `0700`. Metadatos, log y
exportación reciben permisos restrictivos `0600` mediante el código.

### 17.1 Formato CSV

Cada operador recibe dos archivos con un solo encabezado y estructura rectangular. El
perfil `muse` sólo contiene `eeg_logs`, `imu_logs` y `ppg_logs`. El perfil completo
añade metadata, periodos, secciones y ground truth. La columna `record_type` distingue
los tipos de fila y nunca se incluyen señales de otro operador. Las columnas que no
aplican a una fila permanecen vacías. Los valores con comas, comillas o saltos se
protegen mediante las reglas estándar de CSV. La marca BOM UTF-8 permite que Excel
detecte correctamente los acentos.

Las muestras se exportan por `id`, conservando el orden de inserción. La exportación se
escribe primero como archivo temporal y sólo se renombra al terminar.

Para sesiones largas el CSV puede ser mucho mayor que SQLite y tardar en generarse.
Actualmente la GUI no presenta porcentaje de progreso.

---

## 18. Privacidad, seguridad y gobernanza

### 18.1 Medidas actuales

- Los datos permanecen en la computadora central; el servidor sólo se comparte en LAN.
- No existe integración activa con Supabase.
- La GUI escucha en las interfaces LAN y usa claves persistentes separadas para
  investigador y participante, almacenadas con permisos privados.
- Las operaciones sensibles exigen clave de investigador; la clave de participante sólo
  permite estado mínimo, protocolo y envío de ground truth.
- Las mutaciones exigen `X-Muse-Request: muse-web-ui` y Origin del mismo host.
- Se validan nombres de sesión, operadores, señales y lista `hci`.
- Los comandos se construyen como listas, sin concatenar shell arbitrario.
- El preview no acepta consultas SQL del usuario.
- Se recomiendan códigos seudónimos en lugar de nombres.
- Los directorios de sesión son privados para la cuenta de Linux.

### 18.2 Límites de seguridad

- SQLite, JSON, logs y CSV no están cifrados en reposo.
- Una persona con acceso a la cuenta de Linux puede leerlos.
- Las claves viajan por HTTP dentro de la LAN; debe utilizarse una red confiable. HTTPS
  sería obligatorio antes de cualquier exposición fuera de ella.
- `pipeline.log` y los estados pueden contener direcciones MAC, que son identificadores
  persistentes de hardware.
- Los logs hijos en `/tmp/muse_operador_x.log` no forman parte del directorio privado de
  sesión y deben revisarse en una política de endurecimiento.
- Instalar la aplicación no oculta automáticamente el código Python. Empaquetado,
  ofuscación o distribución binaria es un proyecto separado y no equivale a seguridad
  criptográfica.

Para datos humanos sensibles se recomienda además cifrado de disco, cuentas separadas,
copias de seguridad cifradas, consentimiento informado, periodos de retención y una
tabla de seudónimos almacenada fuera del equipo de adquisición.

---

## 19. Verificación y pruebas actuales

### 19.1 Evidencia de pruebas físicas realizadas

En pruebas locales reportadas con dos diademas simultáneas se confirmó:

- aparición de `muse_operador_a` y `muse_operador_b`;
- tópicos EEG, IMU y PPG para ambos operadores;
- inserciones de las tres modalidades para ambos operadores;
- reconexión sin intercambiar inmediatamente la identidad dentro de la sesión;
- funcionamiento de IMU alrededor de 49–50 Hz;
- funcionamiento de PPG alrededor de 62–65 Hz;
- EEG observado por `ros2 topic hz` alrededor de 133–144 Hz.

IMU y PPG se aproximan a 52 y 64 Hz nominales. EEG permanece por debajo de sus 256 Hz
nominales en la medición del tópico. Esto no impide afirmar que existe flujo EEG, pero sí
impide asumir que se están conservando todas las muestras sólo porque la conexión indique
`STREAMING`. Antes de una campaña científica grande se deben comparar, durante la misma
ventana, muestras decodificadas, recibidas, publicadas y filas SQLite, y determinar si la
diferencia proviene de decodificación, entrega por lotes, publicación ROS, medición de la
herramienta o pérdida real.

La arquitectura admite cuatro adaptadores y cuatro operadores, pero el soporte de código
no sustituye una prueba física continua de cuatro diademas. Esa prueba sigue formando
parte del plan de aceptación.

También se realizaron pruebas de operación prolongada en las que una primera sesión de
más de una hora se mantuvo estable, mientras una prueba posterior presentó diademas que
dejaban de transmitir a media sesión. Conectar alimentación externa al hub descartó que
la potencia del hub fuera la única explicación. A partir de esa evidencia se reforzaron
la detección de desconexión GATT, el bombeo del loop BLE, el keepalive, el timeout por
ausencia de EEG, la supervisión del proceso hijo, el backoff coordinado y la recuperación
de adaptadores retirados y reinsertados. Las pruebas posteriores confirmaron reconexión,
pero todavía falta una campaña cuantitativa de cuatro diademas para estimar tasa de falla.

### 19.2 Cobertura automatizada

La suite automatizada cubre:

- reloj local monótono aun cuando el tick Athena no avanza;
- extracción de MACs y exclusión de dispositivos removidos;
- identificación por nombre, UUID, fabricante y OUI;
- rechazo de dispositivos ajenos;
- selección del runtime Python;
- escritura EEG por lotes;
- migración de columnas IMU;
- escritura PPG de 16 canales;
- ausencia de inserciones cuando la grabación está pausada;
- sanitización de nombres de sesión;
- rechazo de entrada `hci` con contenido de shell;
- creación del comando ROS fijo;
- intervalos start/stop sin detener el pipeline;
- reconstrucción de estado, batería y tasas desde logs;
- preview SQLite;
- exportación CSV rectangular, UTF-8 y quoting;
- exportación Excel heredada y protección contra fórmulas.

En la última validación de desarrollo se ejecutaron 23 pruebas funcionales focalizadas,
además de compilación de Python, validación de JavaScript y lint sobre los módulos
modificados.

### 19.3 Lo que las pruebas automatizadas no demuestran

Las pruebas automatizadas no reemplazan:

- una sesión física de cuatro diademas durante 30–60 minutos;
- pruebas de apagar y encender cada diadema en distintos órdenes;
- medición de pérdidas con CPU y disco bajo carga;
- validación del porcentaje de batería contra el indicador real;
- comparación temporal contra una señal común de referencia;
- validación fisiológica de PPG, BPM o HRV.

---

## 20. Limitaciones conocidas

### 20.1 Funcionales

- PPG está activo, pero no hay BPM, HR ni HRV derivados.
- No hay indicador de calidad de contacto EEG.
- No hay detección automática de artefactos.
- No hay tópico de marcadores de experimento o estímulos.
- No hay grabación `rosbag`.
- No hay visualización de ondas EEG en tiempo real; la GUI muestra tasas y filas.
- No hay selección independiente de modalidades: se guardan juntas.
- Sólo puede existir una sesión administrada por la GUI a la vez.
- El mapeo MAC-operador no sobrevive a una nueva sesión.
- No hay carga automática a nube ni sincronización entre computadoras.

### 20.2 Técnicas

- La batería depende de un atributo privado de `muselsl`.
- El estado de la GUI se reconstruye desde log, no mediante una conexión ROS directa.
- El scan se detiene cuando no hay adaptadores libres.
- La columna EEG `channel_5` es herencia de esquema y siempre vale cero con Athena.
- No existen índices SQL adicionales a las claves primarias.
- El conteo exacto del preview puede volverse costoso en bases muy grandes.
- La exportación CSV puede tardar y carece de progreso o cancelación.
- `muse_hrc/package.xml` y `setup.py` todavía declaran versión `0.0.0`, licencia y
  descripción como `TODO`; debe corregirse antes de una distribución formal.
- No existe todavía un instalador cerrado ni protección del código fuente.

### 20.3 Validación temporal

El reloj local y la monotonía entre lotes tienen pruebas unitarias, pero para afirmar
sincronía científica entre dispositivos se requiere un protocolo físico específico.
Las bases históricas deben auditarse: una sesión presenta timestamps comprimidos cuando
`MAX(timestamp)-MIN(timestamp)` es incompatible con su duración real.

### 20.4 Red local y acceso de participantes

- La GUI LAN depende de que el router permita tráfico directo entre computadoras.
- mDNS puede no atravesar VLAN, aislamiento Wi-Fi o filtros de multicast.
- Una dirección entregada por DHCP puede cambiar entre sesiones.
- El alias puede anunciar la interfaz equivocada cuando el anfitrión tiene Wi-Fi y una
  dirección manual `192.168.0.x` al mismo tiempo.
- Las rutas Ethernet creadas para el robot no afectan destinos `10.22.x.x`, pero sí
  capturan cualquier URL que resuelva dentro del mismo `192.168.0.0/24` o
  `192.168.1.0/24` conectado al robot.
- `NetworkError when attempting to fetch resource` indica un fallo de transporte o
  resolución; una clave incorrecta produce HTTP 401 y constituye un problema diferente.
- La versión actual no puede garantizar acceso entre laboratorios con políticas de red
  distintas.

---

## 21. Áreas de oportunidad y recomendaciones de evolución

### Prioridad alta antes de una campaña grande

1. Prueba de estrés con cuatro diademas durante al menos una hora.
2. Medir pérdida por señal comparando recibidos, publicados y filas persistidas.
3. Validar timestamps con un evento físico simultáneo visible en todos los dispositivos.
4. Incorporar marcadores de inicio de condición o estímulo en ROS y SQLite.
5. Definir procedimiento para asociar participante seudónimo con `operador_x`.
6. Crear copia de seguridad automática al finalizar sin borrar el original.
7. Medir reconexión y estabilidad con hub alimentado, cuatro adaptadores y cuatro Muse.
8. Resolver adaptadores por identidad física o dirección del controlador, porque los
   nombres `hciN` pueden cambiar al reiniciar o reconectar el hub.

### Prioridad media

1. Nodo de calidad de señal y contacto.
2. Índices y consultas de resumen por sesión/operador.
3. Barra de progreso y exportación en background.
4. Mover logs hijos a la carpeta privada de sesión.
5. Publicar un mensaje de estado tipado en lugar de JSON para consumidores ROS.
6. Documentar y fijar versiones de firmware, BlueZ, Bleak y `muselsl`.
7. Actualizar metadatos de paquete, versión y licencia.

### Prioridad de investigación

1. Nodo PPG validado para BPM y HRV.
2. Segmentación por ventanas y control de artefactos.
3. Exportación analítica por participante y condición.
4. Integración opcional con marcadores de tarea y robot.
5. Pipeline posterior para PSD, EEGNet, LOSO y xAI.

### Prioridad de seguridad y distribución

1. Cifrado de disco o de archivos de sesión.
2. Política de retención y borrado verificable.
3. Instalador reproducible con versiones bloqueadas.
4. Servicio local bajo una cuenta sin privilegios.
5. Evaluar empaquetado binario sólo después de definir el modelo de amenaza.
6. Separar secretos, datos humanos, bases, CSV y logs del repositorio de código.
7. Añadir autenticación individual, sesiones con caducidad y auditoría antes de publicar
   la GUI en Internet.

---

## 22. Guía breve de operación

### 22.1 GUI

```bash
source /opt/ros/humble/setup.bash
cd /home/fernanda/muse
source install/setup.bash
source web_env/bin/activate
python -m muse_web.app
```

Abrir el enlace de investigador impreso en la terminal y seguir:

1. preparar pipeline;
2. esperar usuarios en “Transmitiendo”;
3. medir señales;
4. comenzar registro;
5. detener registro;
6. finalizar y descargar CSV.

### 22.2 Terminal sin GUI

```bash
source /opt/ros/humble/setup.bash
cd /home/fernanda/muse
source install/setup.bash

ros2 launch muse_hrc muse_system.launch.py \
  hci_devices:=hci1,hci2,hci3,hci4
```

Este modo graba inmediatamente en `~/muse_telemetry.db`, salvo que se pase
`recording_enabled:=false`.

### 22.3 Diagnóstico manual

```bash
ros2 node list
ros2 topic list -t
ros2 topic hz /operador_a/eeg
ros2 topic hz /operador_a/imu
ros2 topic hz /operador_a/ppg
ros2 service list
```

Cada terminal nueva debe cargar `/opt/ros/humble/setup.bash` e `install/setup.bash` para
que ROS 2 conozca los mensajes `muse_msgs`.

---

## 23. Glosario

| Término | Definición en este proyecto |
|---|---|
| Athena | Hardware/firmware Muse S y protocolo BLE usado por estas diademas |
| BLE | Bluetooth de bajo consumo |
| BlueZ | Subsistema Bluetooth de Linux |
| GATT | Servicios y características mediante los que BLE transmite datos |
| MAC | Dirección de hardware observada por Bluetooth |
| `hciN` | Controlador Bluetooth enumerado por Linux |
| EEG | Señal eléctrica del cuero cabelludo |
| PPG | Señal óptica relacionada con cambios de volumen sanguíneo |
| IMU | Acelerómetro y giroscopio |
| Hz | Eventos o muestras por segundo |
| BPM | Latidos por minuto; todavía no se calcula |
| HRV | Variabilidad de la frecuencia cardiaca; todavía no se calcula |
| ROS 2 | Middleware de comunicación entre procesos |
| Nodo | Proceso o componente ROS con una responsabilidad |
| Tópico | Canal continuo de mensajes |
| Servicio | Solicitud/respuesta puntual |
| QoS | Política de entrega y almacenamiento temporal de mensajes |
| SQLite | Base de datos local en un archivo |
| WAL | Log transaccional previo a consolidación de SQLite |
| FIFO | Primero en entrar, primero en salir |
| Watchdog | Comprobación periódica de que un componente sigue vivo |
| Timestamp | Marca temporal de una muestra |
| Payload | Contenido útil de un paquete o mensaje |

---

## 24. Referencia de archivos fuente

| Archivo | Función |
|---|---|
| `muse_hrc/launch/muse_system.launch.py` | Ensambla los nodos base |
| `muse_hrc/muse_hrc/discovery_node.py` | Scan, pool, identidad y procesos |
| `muse_hrc/muse_hrc/ble_identity.py` | Reglas puras de identificación BLE |
| `muse_hrc/muse_hrc/python_runtime.py` | Selección de intérprete Athena |
| `muse_hrc/muse_hrc/athena_adapter.py` | Backend Bleak fijado a `hci` |
| `muse_hrc/muse_hrc/athena_protocol.py` | Reloj local y espaciado temporal por lote |
| `muse_hrc/muse_hrc/muse_node.py` | Decodificación, publicación y reconexión |
| `muse_hrc/muse_hrc/database_node.py` | Puerta de grabación y SQLite |
| `muse_msgs/msg/EegSample.msg` | Contrato EEG |
| `muse_msgs/msg/PpgSample.msg` | Contrato PPG |
| `muse_web/muse_web/session_manager.py` | Ciclo de vida de sesiones |
| `muse_web/muse_web/app.py` | API FastAPI LAN y control de acceso por rol |
| `muse_web/muse_web/static/` | Interfaz de navegador |
| `muse_web/muse_web/csv_export.py` | Exportación CSV actual |
| `muse_web/muse_web/excel_export.py` | Exportación Excel heredada |
| `muse_hrc/test/` y `muse_web/test/` | Pruebas automatizadas |
| `.gitignore` | Excluye builds, entornos, secretos y datos de participantes del repositorio |

---

## 25. Historial consolidado de fallas y correcciones

La evolución del pipeline fue guiada por pruebas físicas. La tabla siguiente separa la
causa observada, el cambio aplicado y el estado que debe conservarse como línea base del
repositorio.

| Falla o necesidad observada | Diagnóstico | Corrección incorporada | Estado actual |
|---|---|---|---|
| Era necesario escribir la MAC en cada ejecución | Descubrimiento dependiente de conocimiento manual | Clasificación automática mediante nombre, UUID de servicio, fabricante y prefijos conocidos | Implementado |
| Había adaptadores detectados por USB que no estaban disponibles para la aplicación | La presencia física no garantizaba controlador BlueZ utilizable | Validación de `hci`, dirección del controlador y pool de adaptadores disponibles | Implementado |
| Dos Muse intentaban conectar simultáneamente y aparecían carreras BLE | BlueZ y GATT quedaban en estados transitorios | Cola de conexión, preparación temporal con `bluetoothctl` y liberación antes de Bleak | Implementado |
| Se saltaba `operador_a`, aparecían B/C o había nombres duplicados | Los intentos fallidos consumían identidad y podían dejar procesos anteriores | Identidad reservada por MAC durante la sesión, limpieza de procesos y reutilización al reconectar | Implementado |
| Una segunda diadema encendida después no se incorporaba | El ciclo de scan y el uso de adaptadores no reanudaban correctamente el descubrimiento | Scan periódico sobre adaptador libre, clasificación continua y cola dinámica | Implementado |
| Una Muse reconectada podía cambiar de operador | La identidad dependía del orden de detección | Tabla MAC–operador en memoria durante toda la sesión | Implementado; no persiste entre sesiones |
| Los tópicos EEG/PPG parecían tener tipo inválido | La terminal no había cargado los mensajes compilados del workspace | Documentación de `source /opt/ros/humble/setup.bash` y `source install/setup.bash` por terminal | Resuelto operativamente |
| IMU o PPG no aparecían para todos los operadores | El pipeline original estaba centrado en EEG y el esquema era incompleto | Publicadores tipados, suscripciones dinámicas y tablas para EEG, IMU y PPG | Implementado |
| La base comenzaba a guardar apenas conectaba una Muse | Conexión y adquisición estaban acopladas a persistencia | Servicio `set_recording`, botón comenzar/detener y tabla de periodos | Implementado |
| Se necesitaba abrir el resultado en Excel sin perder el original | SQLite era correcto para captura, pero no para entrega cotidiana | Dos CSV por operador: sólo Muse y Muse + Likert; escritura atómica y UTF-8 BOM | Implementado |
| La evaluación esperaba a otro operador o bloqueaba el avance | El estado del workshop era compartido | Secciones, avance y CSV independientes por operador | Implementado |
| El ground truth sólo se registraba al cambiar de sección | La metodología requiere medición situacional recurrente | Ventanas de diez minutos que conservan la sección activa y numeración por operador | Implementado |
| Se rechazaba una respuesta aunque el preview mostraba EEG | La verificación no estaba delimitada correctamente por operador y ventana | Comparación de IDs EEG al inicio y fin de cada intervalo individual | Implementado |
| Aparecían “Clave de investigador/usuario inválida” | Tokens reiniciados, cookies ligadas a otro hostname o navegadores que no conservaban cookie sobre IP | Archivo persistente privado, cookie `HttpOnly` y respaldo temporal en `sessionStorage` | Implementado para LAN; debe rediseñarse para Internet |
| La página dejaba de actualizar y mostraba `NetworkError` | Pérdida de conectividad, cambio de IP, mDNS/interfaz incorrecta o aislamiento de clientes | Alias mDNS vigilado, impresión de IP de respaldo y selección configurable de interfaz | Mitigado, no garantizado en redes institucionales |
| Algunas Muse dejaban de transmitir a media sesión | Desconexión GATT, loop BLE no bombeado continuamente, ausencia de keepalive o adaptador removido | Callback inmediato, pump a 100 ms, keepalive, timeout de datos, proceso de un intento, backoff coordinado y hotplug | Corregido funcionalmente; falta estrés cuantitativo |
| Las diademas eran detectadas pero no conectaban | Hub, energía, estado BlueZ, batería o enlace GATT podían fallar de forma independiente | Estados y logs diferenciados, espera de liberación BlueZ, reintentos y diagnóstico por adaptador | Mejorado; requiere inventario de hardware/firmware |

### 25.1 Estado final de la versión local respaldada

La versión que se guardará en el repositorio tiene las siguientes capacidades:

- adquisición simultánea EEG, IMU y PPG de Muse S Athena;
- identificación BLE automática sin MAC en la terminal;
- pool de adaptadores y asignación dedicada por diadema;
- identidad de operador estable durante la sesión;
- detección de pérdida, reconexión y backoff;
- conexión separada de la decisión de grabar;
- SQLite local transaccional con WAL y lotes;
- sesiones independientes, múltiples intervalos y dos perfiles CSV por operador;
- GUI de investigador y vista limitada de participante;
- escala Likert recurrente cada diez minutos, asociada a operador y sección;
- preview, tasas de tópicos, grafo ROS y tail de diagnóstico;
- claves persistentes para uso LAN y alias mDNS configurable.

No deben interpretarse como terminadas la distribución por Internet, la migración ARM64,
el cálculo de frecuencia cardiaca/HRV, la validación científica de sincronía ni la prueba
de aceptación con cuatro Muse durante varias horas.

## 26. Future work aprobado: Raspberry Pi 5 y servicio online

### 26.1 Decisión de plataforma

La plataforma objetivo será una **Raspberry Pi 5 con SSD NVMe**, hub USB alimentado,
fuente adecuada y refrigeración activa. Se propone Ubuntu Server 24.04 ARM64 y ROS 2
Jazzy. No se considera la Pi una simple computadora más: será un dispositivo de borde
administrado por el proyecto y transportable entre laboratorios.

Se eligió Pi 5 + Jazzy en lugar de adquirir una Pi anterior para conservar Humble porque:

- Ubuntu 24.04 soporta oficialmente Raspberry Pi 5;
- ROS 2 Jazzy ofrece binarios ARM64 sobre Ubuntu 24.04;
- proporciona mayor margen para cuatro procesos BLE, ROS, SQLite y supervisión;
- evita iniciar una distribución nueva sobre Ubuntu 22.04/Humble próximos al final de su
  periodo de soporte estándar;
- NVMe reduce el riesgo y la variabilidad de escritura respecto de usar microSD como
  almacenamiento principal de sesiones.

### 26.2 Arquitectura objetivo

```text
Navegador participante ── HTTPS ──┐
                                  ├── Servicio web público
Navegador investigador ─ HTTPS ───┘            │
                                                │ WSS saliente autenticado
                                                ▼
                                   Raspberry Pi 5 / Muse Edge
                                   ├── BlueZ y adaptadores USB
                                   ├── adquisición Muse Athena
                                   ├── ROS 2 Jazzy
                                   ├── SQLite en SSD NVMe
                                   └── CSV local por operador
```

El servidor público no intentará acceder directamente al USB remoto. La Raspberry
iniciará una conexión saliente cifrada y recibirá comandos autorizados. Los datos crudos
permanecerán en el SSD; hacia la nube viajarán principalmente estado, tasas, batería,
progreso y respuestas, salvo una descarga explícita autorizada.

### 26.3 Relación con la red del robot

La aplicación pública elimina la necesidad de que las computadoras participantes se
conecten directamente a la IP de la computadora anfitriona. Cada navegador sólo necesita
salida HTTPS hacia un dominio público. La interfaz Ethernet puede conservar
`192.168.0.XX/24` o `192.168.1.XX/24` para uFactory y la Wi-Fi puede conservar DHCP y su
ruta predeterminada.

La solución no corrige una computadora que haya perdido completamente Internet, pero la
configuración declarada —IP y máscara manuales, sin gateway Ethernet— no debería
reemplazar la salida Wi-Fi. Debe verificarse con `route print -4` durante el piloto. La
Raspberry tampoco dependerá de que el Wi-Fi permita comunicación entre clientes: sólo
necesitará una conexión saliente al servicio público.

### 26.4 Paquetes de trabajo

1. Congelar y etiquetar la versión Humble actual como baseline reproducible.
2. Preparar una rama de migración Ubuntu 24.04/ROS 2 Jazzy ARM64.
3. Compilar mensajes y ejecutar pruebas unitarias en la Pi.
4. Validar BlueZ, Bleak y el decoder Athena en ARM64 con una Muse.
5. Escalar progresivamente a dos y cuatro adaptadores.
6. Resolver controladores por identidad estable y no sólo por número `hciN`.
7. Ejecutar pruebas de dos horas, pérdida de Wi-Fi, hotplug, reinicio y corte de energía.
8. Separar el daemon de adquisición del servidor web para que una caída de Internet no
   detenga ROS ni SQLite.
9. Implementar agente WSS autenticado, registro de dispositivo y cola offline.
10. Publicar GUI HTTPS con identidades individuales, roles, auditoría y expiración.
11. Mantener CSV local y habilitar descarga remota sólo bajo autorización.
12. Crear una imagen reproducible de SSD y procedimiento de actualización firmado.

### 26.5 Criterios de aceptación de la siguiente versión

- cuatro Muse transmitiendo durante al menos dos horas;
- ausencia de mezcla de operadores después de desconectar y reconectar;
- adquisición y persistencia continúan si se interrumpe Internet;
- recuperación automática del enlace cloud sin reiniciar la sesión;
- participantes pueden usar la página con Ethernet uFactory y Wi-Fi simultáneos;
- timestamps de adquisición, sección y respuesta quedan trazables;
- reiniciar la GUI no termina el daemon de adquisición;
- ningún dato humano, secreto o CSV se incorpora al repositorio;
- una imagen nueva de Raspberry puede reproducirse desde documentación y versiones
  bloqueadas.

## 27. Conclusión

El pipeline evolucionó de una conexión EEG individual operada por terminal a un sistema
local multimodal y multiusuario. La arquitectura actual separa descubrimiento, adquisición,
comunicación, persistencia y operación. Sus decisiones principales —adaptador dedicado,
cola secuencial de conexión, proceso por diadema, mensajes tipados, lotes SQLite y puerta
manual de grabación— responden directamente a fallas observadas durante pruebas físicas.

El sistema ya es utilizable para adquisición local de EEG, IMU y PPG con varios usuarios,
siempre que se sigan verificando batería, tasas, correspondencia de operadores y calidad
de colocación antes de registrar. La línea base local incluye las correcciones de
reconexión, grabación manual, CSV por operador, ground truth periódico y acceso LAN. La
dependencia de direccionamiento local y de las políticas de la Wi-Fi sigue siendo una
limitación del despliegue actual, no de la adquisición BLE.

La siguiente etapa será convertir la adquisición en un servicio de borde sobre Raspberry
Pi 5 y SSD NVMe, migrar a ROS 2 Jazzy y separar la GUI pública del proceso que controla
las diademas. Con ello las computadoras del taller podrán mantener su Ethernet estático
para uFactory y acceder por Internet a la interfaz, mientras la captura continúa local y
resistente a interrupciones de red.

Este reporte debe actualizarse cada vez que cambien el protocolo Athena, el esquema de
datos, las frecuencias, la forma de asignar operadores o el ciclo de vida de una sesión.
