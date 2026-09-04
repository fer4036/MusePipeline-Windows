# Gateway y GUI cloud

El contenedor cloud no accede a Bluetooth ni guarda EEG. Mantiene conexiones
WebSocket salientes de los agentes, muestra su estado y enruta comandos hacia
la computadora donde están conectadas las Muse.

## Variables obligatorias

```text
MUSE_CLOUD_RESEARCHER_TOKEN=<secreto largo>
MUSE_CLOUD_OPERATOR_TOKEN=<secreto largo diferente>
MUSE_CLOUD_AGENT_TOKENS={"lab-01":"secreto-del-agente"}
```

No guardes estos valores en Git. En Render se agregan en **Environment** como
secretos. El archivo `render.yaml` ya declara los nombres sin valores.

## Prueba local del gateway

Desde la raíz del repositorio:

```bash
export MUSE_CLOUD_RESEARCHER_TOKEN='cambiar-investigador'
export MUSE_CLOUD_OPERATOR_TOKEN='cambiar-participante'
export MUSE_CLOUD_AGENT_TOKENS='{"lab-01":"cambiar-agente"}'
PYTHONPATH=muse_hrc:muse_web python -m muse_web.cloud_app
```

Para una prueba HTTP local usa encabezados `Authorization: Bearer ...`; la
cookie creada por las ligas está marcada `Secure` y se usa en el despliegue
HTTPS real.

## Ligas después del despliegue

Investigador:

```text
https://TU-SERVICIO.onrender.com/?role=pipeline&token=TOKEN_INVESTIGADOR
```

Participante:

```text
https://TU-SERVICIO.onrender.com/?role=operator&agent=lab-01&token=TOKEN_OPERADOR
```

La liga intercambia el token por una cookie `HttpOnly` y redirige a una URL
sin el secreto.

## Limitaciones del incremento actual

- El registro de EEG, IMU, óptica, batería, SQLite y CSV ya permanece local.
- Preparar, iniciar, pausar, cerrar y exportar se puede ordenar desde cloud.
- La encuesta se enruta al agente y se escribe en el SQLite local.
- El gateway conserva estado solo en memoria y debe ejecutarse con una sola
  instancia durante esta etapa.
- Preview, tail de logs y transferencia explícita de CSV todavía pertenecen a
  la siguiente fase.
- Si internet falla, la adquisición iniciada continúa localmente; la cola
  offline de respuestas del navegador todavía está pendiente.
