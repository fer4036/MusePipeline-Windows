# Coordenadas — Estación xArm6

Mapa de posiciones para las prácticas del workshop ORION.

**Robot:** xArm**6** (6 joints) · IP 192.168.1.200 · firmware 2.5.1 · montaje Floor · payload 1.00 kg
**Fuente:** medición en UFACTORY Studio.
**Estado:** mapa de mesa completo. Falta la zona que rompe el brazo.

---

## Cómo funciona la mesa

- **Pickup 1-3** → el usuario coloca los cubos **a mano** en estos puntos. El robot los agarra de ahí.
- **Entorno A-F** → destinos. El robot deja los cubos aquí.

---

## PICKUPS — origen (colocados a mano)

| Nombre | X | Y | Z | Roll | Pitch | Yaw |
|---|---|---|---|---|---|---|
| **Pickup 1** | 274.4 | -113.2 | 31.2 | -180 | -0.1 | -45 |
| **Pickup 2** | 201.8 | -117.5 | 33.7 | -180 | -0.1 | -45 |
| Pickup 3 | 351.5 | -111 | 32.8 | -180 | -0.1 | -45 |

---

## ENTORNOS — destino (el robot los deja aquí)

| Nombre | X | Y | Z | Roll | Pitch | Yaw |
|---|---|---|---|---|---|---|
| **Entorno A** | 198.9 | -44.4 | 34.6 | 180 | 0 | -45 |
| **Entorno B** | 322.8 | -48.4 | 34.2 | 180 | 0 | -45 |
| **Entorno C** | 241.8 | 34.4 | 36.3 | -179.9 | -0.1 | -44.9 |
| **Entorno D** | 371.2 | 19 | 38.5 | 180 | 0 | -45 |
| **Entorno E** | 213.6 | 123.1 | 28.6 | -179.9 | -0.1 | -44.8 |
| **Entorno F** | 346.9 | 114.3 | 29.1 | -179.9 | -0.1 | -44.8 |

---

## Otras posiciones

| Nombre | X | Y | Z | Roll | Pitch | Yaw |
|---|---|---|---|---|---|---|
| Home | 198.9 | 0 | 316.1 | 180 | 0 | -45 |

**Ángulos de joint de referencia**

| Nombre | J1 | J2 | J3 | J4 | J5 | J6 |
|---|---|---|---|---|---|---|
| Pickup 1 | -22.4 | -1.5 | -15.6 | 0.1 | 17 | 22.5 |
| Entorno B | -8.5 | 2.2 | -21.4 | 0 | 19.2 | 36.5 |
| Entorno C | 8.1 | -12.5 | -5.1 | 0 | 17.5 | 53 |
| Entorno D | 2.9 | 7.9 | -30.6 | 0 | 22.7 | 47.9 |
| Entorno E | 29.9 | -9.3 | -5.6 | -0.2 | 14.8 | 74.9 |
| Entorno F | 18.2 | 8.9 | -29.3 | -0.1 | 20.3 | 63.1 |

---

## Notas

- **La Z varía casi 10 mm entre puntos** (28.6 en E, 38.5 en D). La mesa no está pareja: cada punto usa su propia Z, no una global.
- **Z de aproximación = Z + 80 mm.** Calculada, no medida.
- **Yaw ≈ -45° en los 10 puntos.** Se puede fijar en las prácticas y no repetirlo por punto.
- Gripper: valor 300 en el slider al momento de la captura.
- **Layout:** los 3 pickups en una fila lejana (Y ≈ -114). Los 6 entornos en 3 filas: A y B en Y ≈ -46, C y D en Y ≈ +27, E y F en Y ≈ +119. Buena separación espacial — los movimientos pickup → entorno cruzan el eje central.

---

## Zona de falla — la que rompe el brazo

Medir en ORION, no en UFACTORY (hay que confirmar que el agente la manda sin evaluarla y que el controlador aborta).

Nota: no confundir con el Entorno D, que sí es alcanzable.

| X | Y | Z |
|---|---|---|
| — | — | — |

**Mensaje de error del controlador:**

```
(pendiente)
```

**Cómo se rehabilita:**

```
(pendiente)
```

---

## Pendientes

- [x] Pickups 1 a 3
- [x] Entornos A a F
- [x] Home
- [ ] Confirmar que el gripper agarra el cubo en los pickups (¿qué valor de cierre?)
- [ ] Confirmar que suelta bien en los entornos, sin que el cubo se voltee
- [ ] Zona de falla + mensaje de error + cómo se rehabilita
