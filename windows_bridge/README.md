# Puente MetaTrader propio (VPS Windows)

Sustituye a MetaApi. El backend (VPS Linux) habla por HTTP con `mt_bridge.py` en el VPS Windows, y el puente habla con cada terminal:

| Cuenta (`account_id`) | Terminal | Cómo se conecta el puente |
|---|---|---|
| `hfm_v1` | MetaTrader 4 de HFM (esta app, bot HotForex) | Expert Advisor `HFM_Bridge_EA` + archivos JSON en `MQL4\Files\aitb` |
| `hfm_v2` | MetaTrader 4 de HFM (tu otra app) | igual, con su propio terminal y su propia carpeta |
| `pepperstone_v1` | MetaTrader 5 de Pepperstone (bot Pepperstone) | librería nativa `MetaTrader5` de Python |

Archivos de esta carpeta: `mt_bridge.py`, `requirements_windows.txt`, `HFM_Bridge_EA.mq4`, `config_bridge.example.json`, `start_bridge.bat`.

## 1. Preparar Windows

1. Instala **Python 3.12** (64 bits) marcando «Add python.exe to PATH».
2. Instala los terminales, **cada uno en su propia carpeta**:
   - MT4 de HFM para `hfm_v1` y, en otra carpeta distinta, MT4 de HFM para `hfm_v2` (al instalar el segundo cambia la carpeta de destino, p. ej. `C:\MT4_HFM_2`).
   - MT5 de Pepperstone.
3. Abre cada terminal, inicia sesión con su cuenta (contraseña **master**) y marca «Guardar contraseña».
4. Copia esta carpeta al VPS, por ejemplo en `C:\mt_bridge`, abre PowerShell ahí y ejecuta:
   ```powershell
   pip install -r requirements_windows.txt
   ```

## 2. Expert Advisor en cada MT4 (hfm_v1 y hfm_v2)

Repite en **cada** terminal MT4:

1. `Archivo → Abrir carpeta de datos`. Copia `HFM_Bridge_EA.mq4` dentro de `MQL4\Experts`. Apunta la ruta de esa carpeta de datos: la necesitas en el paso 4.
2. Abre MetaEditor (F4), abre `HFM_Bridge_EA.mq4` y pulsa **Compilar** (F7). Debe terminar con 0 errores.
3. En el terminal: `Herramientas → Opciones → Asesores Expertos` → marca «Permitir trading automático».
4. Activa el botón **AutoTrading** de la barra superior (verde).
5. Abre un gráfico cualquiera (p. ej. EURUSD H1) y arrastra `HFM_Bridge_EA` desde el Navegador. En su ventana marca «Permitir trading en vivo». Arriba a la derecha del gráfico debe verse la cara sonriente. **Un solo gráfico con el EA por terminal.**
6. Pestaña `Terminal → Historial de cuenta` → clic derecho → **Todo el historial** (el EA solo ve el historial que esa pestaña tiene cargado).
7. Comprobación: en `MQL4\Files\aitb\` debe aparecer `status.json` y actualizarse cada segundo.

## 3. MetaTrader 5 de Pepperstone

1. Con la sesión iniciada, activa el botón **Algo Trading** (verde).
2. `Herramientas → Opciones → Asesores Expertos` → marca «Permitir trading algorítmico».
3. Localiza el ejecutable, normalmente `C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe`.

## 4. Configuración del puente

1. Copia `config_bridge.example.json` como `config_bridge.json`.
2. Genera un token y pégalo en `"token"`:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
3. En `"allowed_ips"` pon la IP pública de tu VPS Linux (el de la app). El puente rechaza cualquier otra IP.
4. En cada cuenta MT4, `"files_dir"` = `<carpeta de datos de ESE terminal>\MQL4\Files` (en JSON las barras van dobles: `C:\\Users\\...`).
5. En `pepperstone_v1`, `"terminal_path"` = ruta de `terminal64.exe`. `login`, `password` y `server` pueden quedar en `null` si el terminal ya tiene la sesión guardada.
6. Opcionales por cuenta: `symbol_suffix` (p. ej. `"b"`), `symbol_map` (p. ej. `{"XAUUSD": "GOLDb"}`), `server_utc_offset_hours` (déjalo en `null`: el puente lo detecta de los ticks; ponlo a mano, p. ej. `3`, solo si las velas salen con la hora desplazada).

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
- **Órdenes sin respuesta:** si el EA no llega a leer una orden, el puente borra el archivo y nunca se ejecuta tarde. Si la leyó y no contestó, el bot la trata como «resultado desconocido» y la busca por su identificador (comentario `AIT_…`) antes de reintentar.
- **Cierre parcial en MT4:** MetaTrader da un ticket nuevo al resto de la posición. El puente recuerda la relación en `state\<cuenta>.json` para que el bot siga viendo la misma posición; no borres esa carpeta con posiciones abiertas.
- **ECN / stops:** si el bróker rechaza una orden con SL/TP incluidos, el EA la envía sin stops y los coloca al instante; si no puede colocarlos, cierra la orden. Nunca deja una posición sin stop loss.
- **Más de una cuenta MT5:** se admite; cada una corre en su propio proceso. Solo añádela en `accounts` con su `terminal_path`.
