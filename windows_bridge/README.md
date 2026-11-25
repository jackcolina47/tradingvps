# Puente MetaTrader propio (VPS Windows)

Sustituye a MetaApi. El backend (VPS Linux) habla por HTTP con `mt_bridge.py` en el VPS Windows, y el puente habla con cada terminal:

| Cuenta (`account_id`) | Terminal | Cómo se conecta el puente |
|---|---|---|
| `hfm_v1` | MetaTrader 5 de HFM (esta app, bot HotForex) | librería oficial `MetaTrader5` de Python |
| `hfm_v2` | MetaTrader 5 de HFM (tu otra app) | igual, con su propio terminal |
| `pepperstone_v1` | MetaTrader 5 de Pepperstone (bot Pepperstone) | igual |

Las tres cuentas operan solo con MetaTrader 5. Ya no hay Expert Advisor ni archivos JSON intermedios.

Archivos de esta carpeta: `mt_bridge.py`, `requirements_windows.txt`, `config_bridge.example.json`, `start_bridge.bat` y `tests/` (pruebas, no hace falta copiarlas a Windows).

## 1. Preparar Windows

1. Instala **Python 3.12** (64 bits) marcando «Add python.exe to PATH».
2. Instala los tres terminales MetaTrader 5, **cada uno en su propia carpeta** (detalle en el apartado 2).
3. Abre cada terminal, inicia sesión con su cuenta (contraseña **master**) y marca «Guardar contraseña».
4. Copia esta carpeta al VPS, por ejemplo en `C:\mt_bridge`, abre PowerShell ahí y ejecuta:
   ```powershell
   pip install -r requirements_windows.txt
   ```

## 2. Los tres terminales MetaTrader 5

Cada cuenta necesita **su propia instalación** de MetaTrader 5 (su propio `terminal64.exe`). Dos cuentas no pueden compartir terminal.

| Cuenta | Carpeta de instalación |
|---|---|
| `hfm_v1` | `C:\MT5_HFM_1\terminal64.exe` |
| `hfm_v2` | `C:\MT5_HFM_2\terminal64.exe` |
| `pepperstone_v1` | `C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe` |

1. Instala el MT5 de HFM dos veces, eligiendo en el instalador («Configuración») las carpetas `C:\MT5_HFM_1` y `C:\MT5_HFM_2`.
2. Abre cada terminal, inicia sesión en su cuenta y marca «Guardar contraseña».
3. En cada uno: activa el botón **Algo Trading** (verde) y en `Herramientas → Opciones → Asesores Expertos` marca «Permitir trading algorítmico».
4. Abre `Ver → Observación del mercado`, clic derecho → **Mostrar todo**, para que estén disponibles todos los símbolos.
5. Ya no hace falta ningún Expert Advisor ni carpeta `MQL4\Files`: si tenías `HFM_Bridge_EA` del puente anterior, puedes borrarlo junto con los terminales MT4.

## 3. Cómo conviven las tres cuentas

La librería `MetaTrader5` de Python solo puede estar conectada a un terminal por proceso. Por eso el puente arranca **un proceso propio por cuenta**:
cada uno se conecta una única vez a su `terminal_path` y nunca cambia de terminal. Las cuentas no se esperan entre sí, y si un terminal se cuelga
solo se reinicia su proceso. Si rellenas `login` en una cuenta, el puente comprueba que ese terminal está de verdad en esa cuenta y se niega a operar si no coincide.

## 4. Configuración del puente

1. Copia `config_bridge.example.json` como `config_bridge.json`.
2. Genera un token y pégalo en `"token"`:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
3. En `"allowed_ips"` pon la IP pública de tu VPS Linux (el de la app). El puente rechaza cualquier otra IP.
4. En cada cuenta, `"terminal_path"` = ruta de su `terminal64.exe` (en JSON las barras van dobles: `C:\\MT5_HFM_1\\terminal64.exe`). Si no pones la sección `accounts`, el puente usa las tres rutas de la tabla de arriba.
5. `login`, `password` y `server` pueden quedar en `null` si el terminal ya tiene la sesión guardada. Recomendado: pon al menos `login` (el número de cuenta) como comprobación de seguridad.
6. Opcionales por cuenta: `symbol_suffix`, `symbol_map` (p. ej. `{"XAUUSD": "GOLDb"}`), `deviation_points`, `portable` (`true` si arrancas ese terminal con `/portable`) y `server_utc_offset_hours` (déjalo en `null`: el puente lo detecta de los ticks).

### Símbolos: Demo y Real sin tocar nada

El bot pide siempre el nombre estándar (`EURUSD`, `XAUUSD`, `XAGUSD`) y el puente busca cómo se llama en cada cuenta:

| El bot pide | Cuenta sin sufijo (Demo Premium) | Cuenta con sufijo (Real Zero) |
|---|---|---|
| `EURUSD` | `EURUSD` | `EURUSDb` |
| `XAUUSD` | `XAUUSD` | `GOLDb` |
| `XAGUSD` | `XAGUSD` | `SILVERb` |

También funciona al revés (pedir `EURUSDb` o `GOLDb` en una cuenta sin sufijo). Al pasar de Demo a Real no hay que cambiar la configuración: basta reiniciar el puente o pulsar «Probar conexión» en el panel. Si un bróker usa un nombre fuera de lo normal, añádelo en `symbol_map`.

## 5. Firewall de Windows

Abre el puerto 8000 **solo** para tu VPS Linux (PowerShell como administrador):

```powershell
New-NetFirewallRule -DisplayName "MT Bridge 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -RemoteAddress IP_DE_TU_VPS_LINUX -Action Allow
```

Si tu proveedor tiene un firewall propio en su panel, abre ahí también el 8000 para esa IP.

## 6. Arrancar y comprobar

```powershell
cd C:\mt_bridge
python mt_bridge.py
```

En otra ventana, desde el propio Windows:

```powershell
curl.exe -H "Authorization: Bearer TU_TOKEN" http://127.0.0.1:8000/health
```

Cada cuenta debe salir con `"state":"DEPLOYED"` y `"connectionStatus":"CONNECTED"`. Si una sale `UNDEPLOYED`, el campo `detail` dice qué falta. El registro queda en `mt_bridge.log`.

Desde el VPS Linux:

```bash
curl -H "Authorization: Bearer TU_TOKEN" http://94.72.105.219:8000/health
```

## 7. Que arranque solo

MetaTrader necesita una sesión de escritorio abierta, así que:

1. Configura el inicio de sesión automático del usuario de Windows (`netplwiz`).
2. Pon accesos directos de los tres terminales en la carpeta de Inicio (`Win+R` → `shell:startup`).
3. Programador de tareas → Crear tarea → Desencadenador «Al iniciar sesión» → Acción: `C:\mt_bridge\start_bridge.bat` (relanza el puente si se cierra).
4. No cierres la sesión de Escritorio Remoto con «Cerrar sesión»: usa «Desconectar».

## 8. Conectar la app

En el panel web, para cada bot: **Ajustes → HotForex** (cuenta `hfm_v1`) y **Ajustes → Pepperstone** (cuenta `pepperstone_v1`):

1. URL del puente: `http://94.72.105.219:8000` · Token: el de `config_bridge.json` · ID de cuenta.
2. **Guardar → Probar conexión → Comprobar símbolos.**
3. Con todo en verde, cambia a modo REAL en «Modo de operación».

Alternativa por variables de entorno del backend (el panel tiene prioridad si se rellena): `MT_BRIDGE_URL`, `MT_BRIDGE_TOKEN`, `MT_BRIDGE_ACCOUNT_HFM` (por defecto `hfm_v1`), `MT_BRIDGE_ACCOUNT_PEPPERSTONE` (por defecto `pepperstone_v1`).

Tu otra app usa el mismo puente con `hfm_v2` y el mismo token; añade su IP a `allowed_ips`.

## Qué conviene saber

- **La conexión es HTTP sin cifrar.** El token viaja en claro; la protección es la lista de IPs y el firewall. No abras el puerto a todo Internet.
- **Órdenes sin respuesta:** si el terminal no contesta a una orden en 60 s, el puente responde «timeout», reinicia el proceso de esa cuenta y el bot comprueba por el identificador de la orden si llegó a ejecutarse.
- **Cierres parciales:** en MT5 la posición conserva su número tras un cierre parcial; no hace falta ningún seguimiento extra.
- **Más cuentas:** añade otra entrada en `accounts` con su propio `terminal_path`; corre en su propio proceso.
- **MT4 ya no está soportado:** una cuenta con `"platform": "mt4"` hace que el puente no arranque y lo indique en el mensaje.
