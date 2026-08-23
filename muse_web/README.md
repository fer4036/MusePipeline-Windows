# Muse Research Local

Interfaz web local-first para preparar el pipeline con Python directo o ROS 2,
supervisar diademas y controlar explícitamente los periodos de grabación desde
la misma red local.

## Inicio

```bash
source /opt/ros/humble/setup.bash
cd /home/fernanda/muse
source install/setup.bash
source web_env/bin/activate
python -m muse_web.app
```

Al iniciar, la terminal imprime dos enlaces privados con claves separadas:

- **Investigador**: abre el control completo del pipeline.
- **Participantes**: abre únicamente la evaluación del operador.

Los enlaces usan `http://muse-research.local:8765`. La aplicación publica este
alias mediante Avahi y lo actualiza automáticamente si DHCP cambia la IP de la
computadora. También imprime la IP actual como respaldo para dispositivos que
no soporten mDNS. No uses una dirección IP guardada de una sesión anterior.

En la red manual del laboratorio, la aplicación prioriza automáticamente una
dirección asignada dentro de `192.168.0.0/24`, incluso si el Wi-Fi conserva otra
ruta predeterminada. Para fijar una dirección concreta o cambiar la subred:

```bash
export MUSE_WEB_LAN_IP='192.168.0.20'
export MUSE_WEB_LAN_NETWORK='192.168.0.0/24'
```

La dirección indicada debe estar realmente asignada a una interfaz de la
computadora; estas variables seleccionan la IP que publica la aplicación, pero
no cambian la configuración de NetworkManager.

Comparte el segundo enlace con teléfonos, tabletas o computadoras conectados a
la misma red Wi-Fi/LAN. Sólo la computadora central necesita el runtime Athena,
adaptadores Bluetooth y acceso a las diademas. ROS 2 sólo es obligatorio cuando
se selecciona ese backend. No configures port forwarding en el router:
el servidor está diseñado para una red local confiable, no para Internet público.

Al abrir uno de los enlaces, el servidor valida la clave y crea una cookie HttpOnly
válida durante 12 horas. También entrega un respaldo temporal a JavaScript para
navegadores móviles que rechazan cookies sobre una IP local; se elimina de la barra
inmediatamente. Se debe compartir el enlace completo la primera vez, incluyendo `role`
y `token`.

Las claves se crean una vez en `~/.config/muse-research/access.json`, con permisos
privados, y permanecen estables entre reinicios. Para sustituirlas manualmente, define:

```bash
export MUSE_RESEARCHER_TOKEN='una-clave-larga-y-privada'
export MUSE_OPERATOR_TOKEN='otra-clave-larga-para-participantes'
```

## Flujo de una prueba

La página tiene dos pestañas. **Operador · evaluación** es la vista inicial y
sólo muestra el protocolo y el cuestionario. **Investigador · control del
pipeline** contiene conexión, grabación, señales, diagnósticos y archivos.

1. En la pestaña del investigador, completar código, experimento, adaptadores y
   backend. Usar **Python directo** para captura autónoma o **ROS 2** cuando se
   necesite comunicación con el robot.
2. Presionar **Preparar pipeline y conectar**. En esta etapa se reciben datos,
   pero no se guardan muestras.
3. Confirmar que los usuarios estén en estado **Transmitiendo** y revisar sus
   frecuencias.
4. Presionar **Comenzar a registrar** al iniciar el protocolo experimental.
5. Cambiar a **Operador · evaluación**. El participante selecciona su
   `operador_x` y presiona **Iniciar esta sección** justo antes de comenzar.
6. A los 10 minutos desde el primer paso aparece la primera medición. El participante responde los
   cuatro reactivos y presiona **Enviar medición**. La escala se limpia, la
   sección permanece activa y la siguiente medición vence 10 minutos después.
   Cambiar de paso no reinicia esta cadencia.
7. Al cambiar de actividad, presiona **Terminar esta sección** y después inicia
   el siguiente paso. Cada operador avanza de forma independiente. Si una
   medición ya venció, debe responderse antes de poder cerrar la sección.
8. En la pestaña del investigador, presionar **Detener registro** al terminar.
   Las diademas siguen conectadas y
   se puede volver a iniciar otro intervalo dentro de la misma sesión.
9. Presionar **Finalizar sesión y crear CSV** para detener el backend y generar
   el archivo descargable.

Con Python directo, el supervisor repite el scan cada 15 segundos mientras
exista un adaptador libre. Una Muse encendida a mitad de la sesión recibe el
siguiente `operador_x` y se conecta sin reiniciar la captura existente. Para
incorporarla debe existir un adaptador adicional libre; el controlador usado
temporalmente para escanear se convierte después en su enlace dedicado.

Después de cada conexión o reconexión, el supervisor reserva cinco segundos de
estabilización antes de permitir otro enlace o scan. Una recuperación exitosa
reinicia el backoff: una caída posterior vuelve a reintentarse desde cinco
segundos, en vez de conservar penalizaciones de fallos anteriores. El panel
muestra MAC, adaptador y contadores de desconexión/reconexión para comprobar que
la identidad `operador_x` no cambie durante la sesión.

Si una diadema asignada se apaga, el supervisor no intenta abrir inmediatamente
una conexión GATT larga. Primero comprueba con un scan corto que la misma MAC
volvió a anunciarse en su adaptador dedicado. Mientras no aparezca, muestra
**Esperando diadema** y repite la comprobación cada 15 segundos sin perturbar
los streams de los demás operadores.

En modo Python directo, reiniciar solamente el servidor web tampoco termina el
colector. Al volver a iniciar la página, ésta recupera la sesión mediante
`metadata.json` después de validar PID, comando, base de datos y archivo de
control. Para cerrar realmente la adquisición y crear los CSV se debe usar
**Finalizar sesión y crear CSV**.

## Estabilidad de adaptadores Bluetooth USB

El arranque advierte en `pipeline.log` si Linux mantiene un adaptador con
`power/control=auto`. Para estaciones de adquisición conectadas a corriente se
recomienda desactivar permanentemente la autosuspensión de `btusb`:

```bash
echo 'options btusb enable_autosuspend=0' | \
  sudo tee /etc/modprobe.d/btusb-no-autosuspend.conf
sudo reboot
```

Esto afecta a los controladores Bluetooth USB de la computadora y aumenta
ligeramente su consumo. No se aplica automáticamente desde la página porque
requiere privilegios administrativos. Para cuatro radios simultáneos, usa un
hub alimentado, evita encadenar hubs y separa físicamente los dongles mediante
extensiones USB cortas para reducir interferencia en 2.4 GHz.

No se puede iniciar un paso si la grabación Muse está detenida ni detener la
grabación mientras algún operador mantiene un paso abierto. Para cada operador,
los seis pasos se registran en el orden de **Pick and Place en el robot físico
xArm 6** (`pick-and-place-physical`). La duración global declarada es de 60
minutos; los objetivos individuales suman 65 minutos.

## Ground truth sincronizado

El instrumento usa los cuatro reactivos de situational cognitive engagement de
Rotgans y Schmidt (2011), DOI `10.1007/s10459-011-9272-9`, con escala Likert de
1 a 5. Cada respuesta queda ligada a un `operador_x`, al paso que está activo
cuando se responde y a una ventana consecutiva de 10 minutos. La línea temporal
de `workshop_sections` permite saber si esa ventana atravesó un cambio de paso.

Las tablas `workshop_sections` y `ground_truth_responses` guardan tiempos Unix
en segundos obtenidos de `CLOCK_REALTIME`, la misma referencia temporal usada
por los timestamps del núcleo y, cuando aplica, publicados en ROS. La
participación se valida
además mediante el avance de identificadores de filas EEG durante cada ventana,
por lo que no depende únicamente del reloj reportado por la diadema. Cada
etiqueta incluye:

- identificador de sección y de su intervalo abierto;
- número de medición global del operador y número dentro de la sección;
- inicio y fin de la ventana de señales a la que describe;
- vencimiento programado de la medición;
- momento exacto en que se envió el cuestionario;
- las cuatro respuestas originales;
- promedio global de engagement;
- promedio de la faceta esfuerzo/persistencia.

Para entrenar un modelo, las muestras de un operador se asocian con una etiqueta
cuando su `timestamp` está entre `window_started_at` y `window_ended_at`. Las
columnas de sección permiten conservar el contexto aunque el estudiante no haya
cambiado de paso entre dos mediciones consecutivas.

## Archivos

Cada sesión se guarda bajo `~/MuseResearch/sessions/<sesión>/`:

- `raw.sqlite`: almacenamiento interno transaccional usado durante la captura.
- `export_operador_a_muse.csv`, etc.: versión estrictamente instrumental con
  filas EEG, IMU y PPG del operador; no contiene metadata experimental,
  secciones, ground truth ni respuestas Likert.
- `export_operador_a_completo.csv`, etc.: versión que añade metadata, periodos
  de grabación, secciones y ground truth a las señales Muse del operador.

Ambas versiones son rectangulares, usan UTF-8 BOM y son compatibles con Excel.
Nunca se mezclan filas de señales de distintos operadores. SQLite conserva toda
la información independientemente del CSV que el investigador descargue.
- `metadata.json`: contexto y periodos de grabación.
- `pipeline.log`: bitácora utilizada por el panel tail.

El archivo SQLite interno no se sustituye por un CSV durante la adquisición:
SQLite tolera escrituras simultáneas y cortes de forma mucho más segura. El CSV
se genera de manera atómica al finalizar para evitar entregar archivos parciales
y permanece en la computadora central.

La batería se muestra cuando Athena ha enviado una muestra de batería. PPG es
la señal óptica cruda; el pipeline todavía no calcula BPM validados.
