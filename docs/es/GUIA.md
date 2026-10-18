# Saga – Guía

[English](../en/GUIDE.md) · [Deutsch](../ANLEITUNG.md) · **Español**

Esta guía te acompaña desde la instalación hasta el uso diario: primero para quien administra Saga (admin),
después para todas las personas que lo usan.

**Contenido**

1. [Requisitos](#1-requisitos)
2. [Instalación](#2-instalación)
3. [HTTPS con proxy inverso](#3-https-con-proxy-inverso)
4. [El área de administración](#4-el-área-de-administración)
5. [Usuarios y registro](#5-usuarios-y-registro)
6. [Grupos y servicios](#6-grupos-y-servicios)
7. [LDAP / Active Directory](#7-ldap--active-directory)
8. [SSO con OpenID Connect](#8-sso-con-openid-connect)
9. [Usar Saga](#9-usar-saga)
10. [Búsqueda web (opcional)](#10-búsqueda-web-opcional)
11. [Copias de seguridad, restauración y actualizaciones](#11-copias-de-seguridad-restauración-y-actualizaciones)
12. [Solución de problemas](#12-solución-de-problemas)
13. [Todos los ajustes del .env](#13-todos-los-ajustes-del-env)

---

## 1. Requisitos

- Un servidor Linux (o VM/LXC) con **Docker** y **Docker Compose** (`docker compose version`)
- Unos 512 MB de RAM y algunos GB de espacio (según las imágenes que tengan tus páginas)
- Para acceder desde fuera: un nombre de dominio y un proxy inverso con HTTPS (ver sección 3)

Saga **no** necesita ningún servicio de inicio de sesión externo: las cuentas locales funcionan desde el primer
momento. LDAP y SSO puedes añadirlos más adelante en el área de administración.

## 2. Instalación

```sh
git clone https://github.com/yggdrasil-sh/saga.git
cd saga
cp .env.example .env
```

Abre el `.env` y rellena al menos estos tres valores:

| Ajuste | Significado | Ejemplo |
|---|---|---|
| `BASE_URL` | Dirección con la que se abre Saga | `https://saga.example.org` |
| `SECRET_KEY` | Clave secreta para sesiones y credenciales cifradas | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | Contraseña de la base de datos (solo interna) | `openssl rand -hex 24` |

> ⚠️ **Guarda bien la `SECRET_KEY` y no la cambies.** Con ella se cifran las credenciales guardadas de
> calendarios, Immich, LDAP y SSO. Con una clave nueva quedan ilegibles (y los usuarios tendrán que volver a
> introducirlas).

Opcional: `ADMIN_USERNAME` y `ADMIN_PASSWORD` (al menos 10 caracteres) para la primera cuenta de administración.

Arrancar:

```sh
docker compose pull        # imagen lista desde GitHub (ghcr.io/yggdrasil-sh/saga)
docker compose up -d
docker compose ps          # db y saga deberían estar "healthy"
```

¿Prefieres compilarla tú (por ejemplo, con cambios propios)? `docker compose up -d --build` construye la imagen
desde el código fuente.

Saga ya está disponible en `http://<servidor>:8080`.

**Primera cuenta de administración:** si no has puesto `ADMIN_PASSWORD`, Saga genera una contraseña en el
primer arranque y la escribe en el log y en el archivo `/data/admin-passwort.txt` del contenedor:

```sh
docker compose logs saga | grep Admin-Konto
```

¿Has olvidado la contraseña o te has quedado fuera? Este comando pone una contraseña aleatoria nueva,
desbloquea la cuenta y le devuelve los permisos de administración:

```sh
docker compose exec saga python run.py admin-passwort          # cuenta "admin"
docker compose exec saga python run.py admin-passwort ana      # otra cuenta
```

Si la cuenta tiene un segundo factor (llave de acceso, app de autenticación), este comando de emergencia también
lo elimina: después se inicia sesión solo con la contraseña nueva y se vuelve a configurar el segundo factor. Si la
política «Exigir 2FA» lo pide, el siguiente inicio de sesión (también en el área de administración) lleva
directamente a la configuración: nadie queda bloqueado. Más comandos de emergencia:

```sh
docker compose exec saga python run.py 2fa-zuruecksetzen ana    # quitar solo el segundo factor (la contraseña se queda, también LDAP)
docker compose exec saga python run.py 2fa-richtlinie           # mostrar la política «Exigir 2FA»
docker compose exec saga python run.py 2fa-richtlinie none      # … desactivarla (none | admins | all)
```

![Página de inicio de sesión](../screenshots/login.png)

## 3. HTTPS con proxy inverso

Saga solo habla HTTP. En producción debe ir detrás de un proxy inverso con HTTPS. Importante:

- `BASE_URL` en el `.env` tiene que ser la **dirección https** con la que se abre Saga.
- `TRUSTED_PROXIES` = IP del proxy, para que Saga vea la dirección real de los visitantes (necesaria para el
  bloqueo tras intentos fallidos). Si el proxy está en el mismo servidor, basta con abrir el puerto solo en
  local (`HOST_BIND=127.0.0.1`) y poner `TRUSTED_PROXIES=*`.
- Las peticiones con otros nombres de host se redirigen a `BASE_URL` (`CANONICAL_REDIRECT=true`).

**Caddy** (obtiene el certificado automáticamente):

```
saga.example.org {
    request_body {
        max_size 12MB                      # petición más grande: subida de archivo (ATTACH_MAX_MB + 2)
    }
    reverse_proxy 127.0.0.1:8080
}
```

**nginx:**

```nginx
server {
    listen 443 ssl http2;
    server_name saga.example.org;
    # ssl_certificate ... / ssl_certificate_key ...
    client_max_body_size 12m;              # petición más grande: subida de archivo (ATTACH_MAX_MB + 2)
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Canal en directo (notificaciones, presencia, actualización en directo):** Saga envía las novedades por una
conexión que permanece abierta (server-sent events, `GET /api/events`), por el mismo puerto que todo lo demás, sin
WebSocket y sin puerto adicional. El proxy no debe almacenar en búfer esta respuesta ni cerrar la conexión al poco
tiempo. Saga ayuda por sí mismo: envía `X-Accel-Buffering: no` y `Cache-Control: no-cache`, y cada 25 segundos una
línea de latido para que no salten los tiempos de inactividad.

- **Caddy:** no hay que hacer nada: `reverse_proxy` pasa `text/event-stream` al instante.
- **nginx:** ya funciona con la configuración de arriba (comprobado: sin búfer, la conexión sigue abierta más allá del
  `proxy_read_timeout` predeterminado de 60 s). Se recomienda, y es necesario si delante hay otro proxy con búfer, un
  bloque propio dentro de `server { … }`:

```nginx
    location /api/events {                 # canal en directo: sin búfer, mantener abierto
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 1h;
    }
```

- **Traefik:** funciona sin ajustes. No pongas un middleware `buffering` en el router ni tiempos de inactividad por
  debajo de 30 segundos.
- **Proxy inverso de NetBird y otros:** están comprobados Caddy 2, nginx 1.27 y Traefik 3.1. Para comprobar tu proxy:
  inicia sesión con dos cuentas en dos navegadores y comparte una página de una con la otra; la campana del otro
  navegador debe mostrar un 1 al instante. En las herramientas de desarrollo (red), `/api/events` queda abierto como
  «eventsource». Si un proxy usa búfer o corta la conexión, Saga pasa por sí mismo a consultar cada 60 segundos: todo
  llega, solo que más tarde.

**Tamaño de las peticiones:** Saga rechaza por sí mismo las peticiones demasiado grandes con 413: en general por
encima de 2 MB (`REQUEST_MAX_MB`), las subidas de archivos por encima de `ATTACH_MAX_MB` (más un pequeño margen del
formulario) y el área de administración por encima de 1 MB. Aun así, conviene poner un límite en el proxy inverso
(como arriba, algo por encima de `ATTACH_MAX_MB`): así las peticiones enormes ni siquiera llegan a Saga. Si subes
`ATTACH_MAX_MB`, sube también el límite del proxy.

## 4. El área de administración

El área de administración funciona en un **puerto propio (8081)** con su propio inicio de sesión. Por defecto
solo es accesible desde el propio servidor (`ADMIN_BIND=127.0.0.1`). Desde tu ordenador llegas con un túnel SSH:

```sh
ssh -L 8081:127.0.0.1:8081 usuario@servidor
# después, en el navegador: http://localhost:8081
```

Para acceder desde tu red local, puedes poner en `ADMIN_BIND` la IP LAN del servidor. **No expongas nunca el
puerto de administración a internet.**

Pueden iniciar sesión todas las cuentas con permisos de administración, locales o de LDAP, aunque las cuentas
locales estén desactivadas en la página de inicio de sesión normal.

![Resumen de administración](../screenshots/admin-uebersicht.png)

En **General** puedes ajustar:

| Ajuste | Efecto |
|---|---|
| Título | Nombre arriba en el panel y en la página de inicio de sesión |
| Permitir registro | Cualquiera que llegue a la página puede crearse una cuenta |
| Inicio de sesión con cuentas locales | Desactivado = la página de inicio de sesión solo ofrece LDAP/SSO |
| Grupos por defecto | Los recibe cada cuenta nueva (p. ej. `s_wiki` para un servicio que todos deben ver) |
| Prefijo de grupo | Prefijo de los grupos de acceso a servicios, por defecto `s_` (ver sección 6) |
| Direcciones internas | Si los usuarios pueden conectar calendarios/Immich de la red local (ver sección 9) |
| Idioma predeterminado | Idioma de la página de inicio de sesión, del área de administración y de todas las cuentas en "Automático"; por defecto: Automático (navegador) |

![Administración: General](../screenshots/admin-allgemein.png)

## 5. Usuarios y registro

En **Usuarios** creas cuentas, pones contraseñas y asignas grupos y permisos de administración.

- Las **cuentas locales** tienen su contraseña en Saga (al menos 10 caracteres, guardada como hash scrypt).
- Las **cuentas LDAP y SSO** se crean normalmente solas en el primer inicio de sesión. Si "crear
  automáticamente" está desactivado, créalas aquí de antemano con el tipo de cuenta LDAP o SSO.
- **Bloquear** cierra al instante todas las sesiones, los inicios de sesión de apps y las claves de app de la
  cuenta.
- **Cerrar sesión en todas partes** ayuda si se ha perdido un dispositivo.
- **Restablecer 2FA** (tarjeta «Segundo factor»): elimina las llaves de acceso, la app de autenticación y los
  códigos de recuperación de una cuenta; solo si la persona lo ha perdido todo y estás seguro de que es ella.
  Quién lo restableció y cuándo aparece en el log y en la página del usuario.
- **Exigir 2FA** (General → «Segundo factor»): a nadie (predeterminado), solo a administradores o a todas las
  cuentas locales y LDAP. Quien esté afectado y aún no tenga segundo factor irá a la configuración tras iniciar
  sesión: nadie queda bloqueado y las apps y sesiones conectadas siguen conectadas. Esto vale también para el
  propio **área de administración**: un administrador sin factor configura una app de autenticación tras la
  contraseña (código QR y luego código) y después entra; las sesiones de administración ya abiertas se mantienen.
  Con «todas», los **nuevos inicios de sesión de apps** (ventana de inicio de sesión de la app, emparejamiento por
  QR) también necesitan un factor. Detalles en la sección 9 «Llaves de acceso e inicio de sesión en dos pasos»;
  comandos de emergencia en la sección 2.
- **Eliminar** borra la cuenta **con todos sus datos** (páginas, tareas, archivos, marcadores, conexiones).
  Mejor bloquear las cuentas externas: si no, en el siguiente inicio de sesión se crearía una cuenta nueva y vacía.

El contenido de los usuarios es privado: ni siquiera los administradores ven las páginas, solo cuántas hay.

![Administración: editar usuario](../screenshots/admin-benutzer-detail.png)

**Registro propio:** activa "Permitir registro" en General. La página de inicio de sesión mostrará entonces un
enlace para registrarse. Las cuentas nuevas reciben los grupos por defecto. Con LDAP activo, nadie puede
registrar un nombre que ya exista en el directorio.

## 6. Grupos y servicios

Los **servicios** son los mosaicos del panel (Nextcloud, Jellyfin, Home Assistant…). Quién ve cada servicio se
controla con grupos:

> Un servicio con el nombre corto **`nextcloud`** es visible para todos los miembros del grupo
> **`s_nextcloud`** (prefijo de grupo `s_` + nombre corto).

Funciona igual con **grupos locales** que con grupos de **LDAP/AD** o del **proveedor SSO**: basta con crear
allí un grupo `s_nextcloud`. Al crear un servicio, Saga crea automáticamente el grupo local correspondiente.

Además, para cada servicio puedes indicar:

- **Más grupos**, p. ej. `familia` o `admins`
- **Para todos los usuarios con sesión iniciada**
- **Icono**: el nombre de [selfh.st/icons](https://selfh.st/icons), p. ej. `jellyfin`, `home-assistant`
- **Categoría** y **orden**
- **Comprobar disponibilidad**: Saga consulta la dirección cada minuto y muestra un punto de estado

![Administración: Servicios](../screenshots/admin-dienste.png)

**Servicios desde LDAP (estilo FreeIPA):** en LDAP puedes activar "Tomar servicios de las descripciones de
grupo". Así, cada grupo con el prefijo cuya descripción tenga esta forma se convierte en un servicio:

```
Texto cualquiera || Nextcloud|https://cloud.example.org|nextcloud|Archivos
```

Varios servicios por grupo: añade más `|| Nombre|URL|Icono|Categoría`.

## 7. LDAP / Active Directory

Los usuarios inician sesión en la página normal con la contraseña de su directorio. Saga vuelve a leer sus
grupos en cada inicio de sesión (y cuando una app renueva su sesión).

1. Abre **LDAP / AD**, elige una **plantilla** (FreeIPA, Active Directory, OpenLDAP) y aplícala.
2. Introduce el **servidor**: `ldaps://ipa.example.org` o `ldap://dc1.example.org` con StartTLS.
3. Introduce una **cuenta de servicio** (solo lectura) y su contraseña.
4. Ajusta la **base de búsqueda** de usuarios y grupos.
5. Escribe abajo un nombre de usuario y pulsa **Probar conexión**: Saga muestra DN, nombre, correo y grupos.
6. **Activa LDAP** y guarda.

| | FreeIPA | Active Directory | OpenLDAP |
|---|---|---|---|
| Servidor | `ldaps://ipa.example.org` | `ldaps://dc1.example.org` | `ldap://ldap.example.org` + StartTLS |
| Cuenta de servicio | `uid=saga,cn=sysaccounts,cn=etc,dc=example,dc=org` | `CN=saga,OU=Servicios,DC=example,DC=org` | `cn=saga,dc=example,dc=org` |
| Base de búsqueda de usuarios | `cn=users,cn=accounts,dc=example,dc=org` | `OU=Usuarios,DC=example,DC=org` | `ou=people,dc=example,dc=org` |
| Grupos | memberOf en el usuario | AD, incluidos grupos anidados | buscar grupos |

Más opciones: solo los **miembros de este grupo** pueden iniciar sesión; los miembros del **grupo de
administración** son administradores. ¿Usas tu propia autoridad de certificación (CA)? Pon el archivo como
`.crt` en la carpeta `certs/`, junto a `docker-compose.yml`, y ejecuta `docker compose restart saga`.

![Administración: LDAP](../screenshots/admin-ldap.png)

## 8. SSO con OpenID Connect

Inicio de sesión con Keycloak, Authentik, Authelia, Zitadel, Entra ID, Google y similares. En el proveedor,
crea un **cliente confidencial** (authorization code, PKCE):

- **URI de redirección:** `https://saga.example.org/auth/callback`
- **URI de redirección tras cerrar sesión:** `https://saga.example.org/login?bye=1`

En Saga, en **SSO / OIDC**, introduce:

| Campo | Ejemplo (Keycloak) |
|---|---|
| URL del emisor (issuer) | `https://sso.example.org/realms/home` |
| ID de cliente / secreto de cliente | del cliente en el proveedor |
| Claim del nombre de usuario | `preferred_username` |
| Claim de grupos | `groups` (en Keycloak, crea un mapper "Group Membership" con "Full group path" desactivado) |

**Probar conexión** comprueba el documento de descubrimiento del proveedor. Después, **activa SSO** y guarda:
en la página de inicio de sesión aparece el botón de SSO. Si las cuentas locales están desactivadas y LDAP no
está activo, la página de inicio de sesión lleva directamente al proveedor.

Los grupos como `s_nextcloud` del proveedor SSO dan acceso a servicios igual que los grupos locales.

**Proveedor SSO con un directorio detrás** (p. ej. Keycloak conectado a FreeIPA o AD): en lugar de configurar
un claim de grupos, Saga puede leer los grupos directamente del directorio. Para ello, introduce el directorio
en **LDAP / AD** y activa **"Leer los grupos de las cuentas SSO desde el directorio"**. Si la página de inicio
de sesión solo debe ofrecer SSO, desactiva "Inicio de sesión con la contraseña del directorio". Las cuentas
bloqueadas en el directorio pierden entonces también al instante el acceso a Saga (en FreeIPA, mediante la
plantilla). **Miembros de este grupo** y **grupo de administración** funcionan igual que con LDAP.

> **Seguridad:** un inicio de sesión SSO o LDAP nunca se apropia de una cuenta existente de otro origen con el
> mismo nombre. Si el registro está abierto, alguien podría ocupar un nombre de antemano: con SSO, deja el
> registro desactivado o crea las cuentas por adelantado.

**Identificador SSO fijo:** en el primer inicio de sesión SSO, Saga guarda el identificador del proveedor
(`sub`). Si más tarde alguien inicia sesión con el mismo nombre de usuario pero otro identificador, se rechaza
("vinculada a otra identidad SSO"). **¿Has cambiado de proveedor** (todos los identificadores son nuevos)? En el
área de administración, abre Usuarios → la cuenta → **"Eliminar vínculo SSO"**: el próximo inicio de sesión SSO
con ese nombre de usuario vuelve a vincular la cuenta y se conservan todos los datos. Hazlo solo si estás seguro
de que en el proveedor es la misma persona quien tiene ese nombre.

![Administración: SSO](../screenshots/admin-sso.png)

## 9. Usar Saga

### Panel

Mosaicos para tus servicios y marcadores, ordenados por categorías. **★** marca un mosaico como favorito.
A la derecha están tus tareas pendientes y los eventos de hoy y mañana. La búsqueda encuentra servicios,
marcadores y, debajo, también tus **páginas** (título y contenido).

| Tecla | Acción |
|---|---|
| `/` | Buscar |
| `1`–`9` | Abrir mosaico |
| `Enter` | Primer resultado (si no, búsqueda web) |
| `t` | Nueva tarea |
| `n` | Páginas |

![Panel](../screenshots/dashboard.png)

### Marcadores

**+ Marcador** abajo en el panel, o en el menú de usuario, en Ajustes. Tus marcadores solo los ves tú, también
en las apps; el administrador solo ve cuántos hay. Basta con la dirección: sin nombre, el mosaico se llama como
el host (p. ej. `wiki.example.org`). Icono: un nombre de [selfh.st/icons](https://selfh.st/icons); si no,
aparece la inicial. Se editan con **✎** en el mosaico.

![Marcadores](../screenshots/lesezeichen.png)

### Tareas

- **Crear:** escribe en **Añadir rápido …** en la barra de la derecha y pulsa `Enter` (la tecla `t` salta allí).
  Si la barra está contraída (**»**) o la ventana es estrecha, las tareas aparecen en el recuadro **Tareas** del
  área principal, nunca en los dos sitios a la vez. Ahí puedes fijar al crearlas la **prioridad**, la
  **repetición** (diariamente, días laborables…) y la **fecha de vencimiento** (iconos en el campo de entrada).
  **Todas las tareas …**, abajo en la barra, abre ese recuadro.
- **Fecha:** con el icono de calendario al crearla o más tarde con **⋯** en la tarea → **Elegir fecha …**. Las
  que vencen hoy muestran «hoy»; las atrasadas aparecen en rojo.
- **Completar:** haz clic en el círculo delante de la tarea. Abajo aparece **«Tarea completada · Deshacer»**;
  durante unos 8 segundos **Deshacer** la recupera. En las tareas repetidas se muestra la próxima fecha, y Deshacer
  también quita la siguiente tarea ya creada.
- **Completadas:** **Mostrar completadas** debajo de la lista. Un clic en la marca vuelve a abrir una tarea,
  **×** la elimina.

Las mismas tareas están en las apps y, como bloque `/Tareas`, también en las páginas.

### Páginas y bases de datos

En **Páginas** está tu árbol de páginas. Escribe `/` para añadir bloques: títulos, listas, listas de
verificación, tablas, código, imágenes, archivos, columnas, enlaces a otras páginas, eventos y más. Si sigues
escribiendo, el menú se filtra; `Enter` elige el primer resultado y `Esc` lo cierra y quita la «/». `Ctrl`+`K`
(Mac: `⌘`+`K`) busca en títulos y contenido. Las páginas tienen versiones y papelera (30 días) y se pueden
exportar como Markdown.
**Copiar imágenes:** un clic en una imagen la selecciona; `Ctrl`+`C`/`X` (Mac: `⌘`) la copia o la corta y `Ctrl`+`V` la
pega en esta u otra página (también en otra pestaña), con su pie de foto. En una página de otra cuenta, su propietario
recibe su propia copia del archivo. Otras aplicaciones reciben la imagen como PNG cuando el navegador lo permite.

![Página](../screenshots/seite.png)

Las **bases de datos** reúnen entradas con propiedades (estado, fecha, número, selección, relación, fórmula…)
y las muestran como tabla, tablero, lista, galería o calendario, con filtros, orden y totales. En una tabla,
`Enter` en el nombre pasa a la fila siguiente; en la última crea directamente la próxima entrada.

![Base de datos como tablero](../screenshots/board.png)

### Publicar páginas

Cualquier página puede hacerse **pública**: se lee sin iniciar sesión, con un diseño sencillo, claro u oscuro según
el dispositivo y cómoda de leer en el móvil. En el menú de la página (**⋯** a la derecha, encima de la página, junto
a «Guardado») elige **«Publicar …»** y activa el interruptor **«Pública»** en el diálogo: desde ese momento la página
está disponible en su dirección. De arriba abajo, el diálogo muestra: el interruptor, el enlace (con Copiar y Abrir;
en el móvil también Compartir), la dirección, las opciones y «Dejar de publicar». Mientras la página es privada, la
dirección futura aparece atenuada y todavía sin enlace, y las opciones están agrupadas bajo «Se aplica cuando la
página sea pública». En una página pública, los cambios en las opciones se aplican al instante; solo una dirección
nueva se aplica expresamente con «Cambiar dirección». Los errores (p. ej., una dirección ya usada) aparecen justo
debajo del campo.

- **Nombre público:** La primera vez eliges un nombre público (sugerido a partir de tu nombre visible, 2–32
  caracteres `a–z 0–9 - _`). Aparece en todas las direcciones y es visible para todos; **nunca** es tu nombre de
  usuario. Se cambia en Ajustes → Cuenta, en su propio bloque «Nombre público» (con su propio botón «Guardar nombre»; Saga
  comprueba mientras escribes si el nombre es válido y está libre, y muestra «Nueva dirección: … (antes …)»);
  después, los enlaces antiguos dejan de funcionar.
  Nombres como `admin`, `api` o `login` están reservados.
- **Dirección:** `https://saga.example.org/@nombre/direccion-de-la-pagina`. La dirección se sugiere a partir del
  título y se puede cambiar (2–60 caracteres): en cuanto lo que escribes difiere de la dirección guardada, aparece
  el botón **«Cambiar dirección»**; tras confirmar, porque el enlace actual deja de funcionar. Saga redirige las mayúsculas o una «/» final a la dirección correcta.
- **Publicar también las subpáginas:** Todas las subpáginas (y las entradas de bases de datos) bajo esta página
  también serán legibles, en `/@nombre/direccion-de-la-pagina/<número>`. Sin esta opción siguen siendo privadas. Esas subpáginas muestran
  «🌐 Pública (a través de «página superior»)» en la cabecera; su diálogo lo explica y lleva a la página superior.
- **Permitir buscadores:** Desactivado por defecto: la página pide entonces a los buscadores que no la indexen.
- **Como página de perfil:** Esta página aparece en `/@nombre` (como máximo una por cuenta). Sin página de perfil,
  `/@nombre` muestra una lista sencilla de tus páginas públicas. Si otra página ya es tu página de perfil, el
  diálogo la nombra: la nueva la sustituye. Si la página es tu página de perfil, el diálogo muestra además, debajo
  del enlace, la **dirección del perfil** `…/@nombre` para copiarla.
- **Copiar enlace / Abrir** (en el móvil también **Compartir**) y **Dejar de publicar** (con confirmación). Las páginas publicadas muestran «🌐 Pública» en la cabecera;
  un clic abre el diálogo y «↗», a su lado, la página pública.
- **Al dejar de publicar se guarda todo:** la dirección y los ajustes se conservan (la dirección queda reservada para
  esta página). Si vuelves a publicarla, usa la misma dirección que antes.

**Qué se publica y qué no:** título, icono, portada y contenido (texto, encabezados, listas, listas de comprobación,
citas, código, imágenes, tablas, columnas, avisos, desplegables). Las bases de datos aparecen como una tabla sencilla
(título y propiedades visibles de la vista). Las inserciones se convierten en enlaces. **No** se publican los bloques
de tareas, calendario e Immich, los botones ni los archivos adjuntos. Los enlaces a otras páginas solo siguen siendo
enlaces si la página de destino también es pública; si no, queda solo el texto. Saga solo entrega imágenes que se
usan exactamente en esta página.

**Cómo ven la página los visitantes:** Debajo del título aparece «@nombre · actualizado el 14/10/2026», y arriba a la
izquierda tu `@nombre` (en la página de inicio para visitantes, el nombre de la instancia). La barra, la fecha y los
mensajes aparecen en el idioma del visitante; el contenido se marca con el idioma de tu cuenta (importante para
lectores de pantalla y la división de palabras). El texto tiene un ancho de lectura cómodo; las imágenes y tablas
pueden ser más anchas. Una dirección que no existe ofrece un siguiente paso: «Ir a la página de inicio» (si el admin
eligió una) o «Iniciar sesión».

**Papelera y archivo:** Las páginas archivadas o en la papelera no son accesibles («No encontrado»). La publicación
se conserva: si recuperas la página, vuelve a ser pública en la misma dirección. Solo desaparece al eliminar la
página definitivamente (o al eliminar la cuenta).

**Para el administrador** (área de administración → General → «Publicar»):

- **Permitir publicar** (activado por defecto). Desactivado = todas las direcciones públicas devuelven «No
  encontrado» al instante; los ajustes de las páginas se conservan.
- **Página de inicio para visitantes:** una página ya publicada que ven en `/` los visitantes sin cuenta (arriba a
  la derecha, «Iniciar sesión»). Los usuarios con sesión iniciada siguen viendo su panel.
- **Publicadas** (pestaña propia): todas las páginas públicas con cuenta, nombre y fecha; **Retirar** termina una
  publicación al instante (tras confirmar). La persona ve en el diálogo «Retirada por el administrador el …»; la
  dirección y los ajustes se conservan.

Cualquier error (nombre desconocido, página privada, publicación desactivada) devuelve la misma página neutral
«No encontrado». Las páginas públicas funcionan sin JavaScript y sin cookies; cada dirección IP puede hacer 120
solicitudes por minuto (`PUBLIC_RATE`). Si Saga funciona detrás de un proxy inverso, añádelo a `TRUSTED_PROXIES`
(sección 3); de lo contrario, todos los visitantes comparten un único límite.

**Dominio propio:** Para mostrar las páginas públicas en, por ejemplo, `blog.example.org`, haz que el DNS del dominio
apunte a tu servidor, redirígelo a Saga en el proxy inverso (como en la sección 3) y añádelo al `.env`:
`PUBLIC_HOSTS=blog.example.org` (después, `docker compose up -d`). `BASE_URL` sigue siendo tu dirección principal.
En el dominio propio Saga solo entrega la página de inicio para visitantes (`/`; elige una en el área de
administración), las direcciones `/@…` y los archivos necesarios; el inicio de sesión y todo lo demás redirige a
`BASE_URL`. Abiertas a través de ese dominio, las páginas lo indican también como dirección principal (`canonical`).
Ejemplo para Caddy:

```
blog.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

### Compartir y comentar

Las páginas y bases de datos se pueden compartir con **otras cuentas** y **grupos** de la misma instancia de Saga.
Usa el botón **«Compartir»** arriba a la derecha de tu propia página (o el menú de la página **⋯** → «Compartir …»):
busca una persona o un grupo (a partir de 3 caracteres; verás el nombre visible y el nombre de usuario, nunca una
lista de todas las cuentas), elige un rol y pulsa el resultado. El diálogo solo ofrece grupos a los que perteneces,
tanto locales como de LDAP o SSO; con el campo de búsqueda vacío aparecen como chips («Tus grupos:»), y una línea bajo
el campo explica qué puede hacer el rol elegido. En la lista de abajo cambias el rol o quitas el acceso (con
confirmación; el acceso termina al instante). La lista muestra el rol real: si alguien tiene más permisos a través de
un grupo, se indica («Comentar (a través de team: Editar)»), y la confirmación al quitarlo nombra el acceso
que se mantiene. Los usos compartidos de páginas superiores aparecen en una sección propia, de solo lectura, con
enlace a la página superior. Abajo puedes copiar el enlace de la página: la abre para todas las personas con acceso.

| Rol | puede |
|---|---|
| **Ver** | leer la página, las subpáginas y las entradas de bases de datos, exportar, leer comentarios |
| **Comentar** | además comentar, responder y resolver conversaciones |
| **Editar** | además cambiar título, contenido, icono, portada, etiquetas y propiedades, crear subpáginas y entradas, subir imágenes |

- **Las subpáginas se incluyen:** un uso compartido vale también para todas las subpáginas y para las entradas de las
  bases de datos que hay debajo. Cuenta el rol más alto (persona o grupo, esta página o una superior). Las páginas
  archivadas, las de la papelera y las plantillas solo las ve el propietario.
- **Solo el propietario puede:** eliminar, archivar, mover, bloquear, marcar como plantilla, publicar, ver y restaurar
  versiones y gestionar el acceso. Lo que otros crean o suben pertenece al propietario de la página; las versiones se
  guardan con él. Así, las personas con «Editar» usan el **espacio del propietario**: sus imágenes y archivos cuentan
  en él.
- **Compartir y publicar:** si una página es pública con «Publicar también las subpáginas» y además está compartida
  con «Editar», las subpáginas e imágenes que creen esas personas se publican de inmediato; el diálogo de publicación
  lo indica.
- **Compartido conmigo:** en la barra lateral, la sección «Compartido conmigo» muestra las páginas que otros han
  compartido contigo (con el nombre del propietario). Tus propias páginas compartidas llevan un símbolo de personas en el árbol. Los
  comentarios nuevos de otras personas se ven como un punto en la página del árbol, como número en «Compartido
  conmigo» y como «💬 n» en el botón «Páginas» de la página de inicio. Si compartir está desactivado en la
  administración, aparece un aviso.
- **Quién cambió qué:** bajo el título ves «Último cambio de Nombre · hace 5 min» (si no fuiste tú) y, en las páginas
  que creó otra persona, «Creada por Nombre». El historial de versiones indica el autor de cada versión guardada.
- **Las páginas de otros** muestran «Compartido por Nombre · Puedes ver/comentar/editar» bajo el título (y debajo, quién
  hizo el último cambio). Si el propietario cambia tu rol, la página lo muestra al guardar o al volver a la pestaña. Con «Ver» o «Comentar» el editor
  es de solo lectura; las bases de datos muestran «Solo lectura» (ordenar y filtrar funciona, pero no se guarda). Los
  bloques de tareas, calendario e Immich solo muestran «Solo visible para el propietario», nunca los datos del
  propietario ni los tuyos. Los enlaces a páginas que no se han compartido contigo aparecen como «Página sin acceso».
- **Editar a la vez:** si dos personas guardan casi al mismo tiempo, aparece el aviso de conflicto de siempre, con
  nombre: «Bert acaba de cambiar esta página». **«Cargar la nueva versión»** carga la versión de Bert y muestra tu
  texto sin guardar para copiarlo. **«Conservar mi versión»** pregunta antes; la versión de Bert queda entonces en el
  historial de versiones del propietario: nada se pierde sin dejar rastro.
- **El acceso termina mientras escribes:** si el propietario quita tu acceso (o lo baja a «Ver», o la
  administración desactiva compartir), un diálogo muestra tu último texto para copiarlo. Los enlaces antiguos a
  páginas sin acceso muestran «Sin acceso a esta página» con «Reintentar» e «Ir a «Compartido conmigo»».

**Los comentarios** van unidos a un bloque y son discretos: en el margen solo aparece un número pequeño (💬 2), y
solo mientras haya conversaciones abiertas. Se comenta desde el menú del bloque (**⋮⋮** a la izquierda del bloque →
«Comentar», o Ctrl/⌘ + / → «Comentar»); al pasar sobre un bloque aparece además un pequeño botón 💬+ (en pantallas táctiles con páginas de solo lectura, tras tocar el bloque). La
conversación se abre en una barra lateral a la derecha (en el móvil, desde abajo). Allí respondes, editas o eliminas
tus comentarios (el propietario de la página puede eliminar todos) y cierras una conversación con **«✓ Resolver»** (queda como «Resuelto»):
se contrae y desaparece; el interruptor **«Mostrar resueltos»** la recupera y «Reabrir» la vuelve a activar. «💬 3» en
la cabecera de la página abre todas las conversaciones; un punto pequeño marca lo no leído. Editar y eliminar un
comentario están en su menú ⋯; el propietario encuentra también todas las conversaciones en el menú de la página **⋯**
→ «Comentarios» (con el número de resueltas). Los comentarios son texto
plano (como máximo 2000 caracteres, se permiten saltos de línea; los enlaces se reconocen), como máximo 30 comentarios
por minuto. También puedes comentar en tus páginas no compartidas, como nota para ti. Si se elimina un bloque, su
conversación queda en la lista como «Bloque eliminado». Si eliminas el primer comentario de una conversación en la
que otros han respondido, las respuestas se mantienen y tu comentario aparece como «Comentario eliminado»; el
propietario de la página puede eliminar toda la conversación (tras confirmarlo).

**Para el administrador** (área de administración → General → «Compartir»): **Permitir compartir** (activado por
defecto; desactivado = todos los usos compartidos dejan de funcionar al instante, pero se conservan) y **Solo dentro
de grupos en común** (desactivado por defecto; búsqueda de personas y compartir solo con cuentas que tienen un grupo
en común contigo; solo afecta a los nuevos usos compartidos: los existentes se mantienen si lo activas más tarde).
Saga toma los grupos de LDAP o SSO al iniciar sesión: si allí cambia la pertenencia, los usos compartidos con grupos
lo reflejan a partir del siguiente inicio de sesión de la cuenta. El detalle del usuario solo muestra el número de
usos compartidos, nunca contenidos. Al eliminar
una cuenta desaparecen sus usos compartidos y los que se le dieron; sus comentarios en páginas de otros quedan como
«Cuenta eliminada». Al eliminar un grupo terminan los usos compartidos con él.

### Compartir con otras instancias de Saga (federación)

Se pueden conectar varios servidores Saga (por ejemplo, el tuyo y el de una asociación). Después, una página también
se puede compartir con **nombre@servidor**: una persona de allí (con su nombre público, ver «Publicar páginas») o un
grupo de allí (**grupo@servidor**). El contenido siempre se queda en el servidor del propietario: la otra persona lee,
comenta y edita a través de su propio servidor Saga, que consulta al servidor del propietario en segundo plano. El rol
lo decide siempre el servidor del propietario; retirar el acceso surte efecto al instante. Todo pasa por el puerto
HTTPS normal; no hay ningún servicio adicional.

**Conectar (administradores de ambos servidores):**
1. Área de administración → **Federación** → «Activar la federación» (por defecto: desactivada). Ahí aparecen tu
   dirección y tu **huella** (forma corta de la clave pública de este servidor).
2. Servidor A: **«Crear código de invitación»**: da la dirección y el código al administrador del servidor B (el código
   vale 24 horas y una sola vez).
3. Servidor B: en «Conectar con una instancia», escribe la dirección y el código → Saga muestra la huella de A. Si
   coincide con la que te dio A (mejor compararla por una segunda vía) → «La huella coincide: conectar».
4. Servidor A: la solicitud aparece en «Instancias conectadas» con la huella de B → **Confirmar**. Ya están
   conectados. Si B no estaba disponible en ese momento, usa allí «Comprobar estado».

**Desconectar** (cualquiera de las dos partes, en cualquier momento) surte efecto al instante: terminan todos los usos
compartidos con personas y grupos del otro servidor y desaparecen las páginas de allí. El **registro de auditoría**
muestra conexiones, usos compartidos entre servidores y solicitudes rechazadas, nunca contenido. Saga solo accede a
direcciones internas (192.168.x, 10.x …) si figuran en General → «Hosts internos permitidos»; los otros servidores
solo se conectan por https.

**Compartir:** en el diálogo de compartir, escribe `nombre@servidor` (o `@nombre@servidor`): Saga pregunta al servidor
conectado si existe la persona o el grupo y lo muestra con su origen («Bert (bert@asociacion.example)»). Para ello
necesitas un nombre público (Ajustes → Cuenta). Las personas de otros servidores aparecen siempre con `@servidor` en
«Compartido conmigo», en los comentarios y en las notificaciones, para que nadie se confunda con una cuenta de aquí del
mismo nombre. Si está activado «Solo dentro de grupos en común», no se puede compartir con otros servidores.

**Recibir:** las páginas de otros servidores aparecen en **«Compartido conmigo»** (con `nombre@servidor`), se abren en
el editor normal y, según el rol, se pueden leer, comentar y editar (título y contenido; si se edita a la vez aparece
el aviso de conflicto de siempre). Las imágenes llegan a través de tu propio servidor. Para ello necesitas un nombre
público. Las notificaciones («ha compartido una página contigo», «ha respondido») también funcionan entre servidores,
sin el texto del comentario.

**Todavía no entre servidores** (en esta versión): abrir bases de datos de otros servidores (aparece un aviso), subir
imágenes a páginas de otros servidores, crear subpáginas, versiones, exportar, «quién está aquí» y actualización en
directo, chat. Quien cambia su nombre público o elimina su cuenta pierde los usos compartidos de otros servidores (se
eliminan allí); el nombre antiguo queda bloqueado para otras cuentas.

### Notificaciones y actualización en directo

Saga mantiene al día el panel y las páginas abiertas, sin recargar.

- **Campana** arriba a la derecha (panel y páginas): el número indica las notificaciones sin leer; un clic abre la
  lista (en el móvil, desde abajo). Recibes una notificación cuando alguien comparte una página contigo o cambia tu rol,
  cuando alguien comenta tu página, cuando alguien responde en una conversación en la que participas y cuando un
  administrador retira una de tus páginas publicadas; nunca por tus propias acciones. Varios comentarios de la misma
  conversación en 10 minutos se agrupan («3 respuestas nuevas en una conversación»). Un clic abre la página (en los
  comentarios, directamente la conversación) y marca la entrada como leída; **«Marcar todo como leído»** lo marca todo.
  El título y el extracto del comentario solo aparecen mientras tengas acceso; después, la entrada muestra «Ya no tienes acceso».
  Las notificaciones se guardan 90 días.
- **Ajustes → Cuenta → Notificaciones:** activa o desactiva cada tipo.
- **Quién está aquí:** si otras personas tienen la página abierta, sus iniciales aparecen en la cabecera («Bert también
  está aquí»); un anillo significa «Bert está editando».
- **La página cambia mientras la tienes abierta:** sin cambios sin guardar, Saga carga la versión nueva en silencio
  (la posición de desplazamiento y el cursor se mantienen en lo posible). Si estás escribiendo, aparece «Anna ha
  cambiado esta página» con **Recargar**; si guardas de todos modos, actúa el aviso de conflicto de siempre.
- Los comentarios, los puntos de no leído, «Compartido conmigo» y «💬 n» en el botón «Páginas» también se actualizan al
  instante.
- Sin conexión en directo (el proxy usa búfer, más de 5 pestañas abiertas de la misma cuenta) Saga consulta cada 60
  segundos. Para el proxy inverso, consulta la sección 3; el área de administración muestra en General el número de
  conexiones en directo activas.

### Chats (cifrado de extremo a extremo)

El botón **«Chats»** en la cabecera del panel y de las páginas (con el número de mensajes sin leer) abre los chats en una
pestaña propia. Todos los mensajes, adjuntos y los nombres de los grupos libres se cifran **en tus dispositivos**: el
servidor solo guarda datos cifrados. Tampoco el administrador puede leer el contenido.

- **Configurar (una vez por cuenta):** la primera vez, Saga crea tus claves y muestra una **clave de recuperación**
  (32 caracteres, p. ej. `ABCD-EFGH-…`). Anótala o guárdala en un gestor de contraseñas («Copiar», «Guardar como
  archivo») y confirma los últimos cuatro caracteres. Sin ella y sin otro dispositivo configurado tus mensajes se
  pierden: tampoco el administrador puede recuperarlos.
- **Dispositivo o navegador nuevo:** o bien **«Confirmar con otro dispositivo»** – en un dispositivo donde los chats ya
  funcionan aparece la solicitud con un código de 20 cifras (cuatro grupos de cinco); confírmala solo si coincide por completo con el código del dispositivo
  nuevo – o **introducir la clave de recuperación**. Después, el dispositivo nuevo también lee el historial. Si no tienes
  ninguna de las dos, puedes **empezar de nuevo** (claves nuevas; los mensajes anteriores ya no se podrán leer).
  Antes, Saga te pide la contraseña; con cuentas SSO vuelves a iniciar sesión (después tienes 5 minutos).
- **Conversaciones:** **«+ Nuevo»** → busca una persona (mín. 3 caracteres) para un **chat directo**, uno de tus grupos
  para un **chat de grupo** (los miembros siguen al grupo, también desde LDAP/SSO) o «grupo con varias personas» para un
  **grupo libre** (quien lo crea invita y elimina; el nombre va cifrado). En páginas compartidas, **«💬 Chat»** en la
  cabecera abre el **chat de la página** con todos los que pueden verla: si cambian los permisos, cambian los miembros.
  Quien se une solo ve los mensajes desde su incorporación; quien sale no recibe nada nuevo.
- **Escribir:** Intro envía, Mayús+Intro añade una línea. 📎 adjunta imágenes o archivos (cifrados, hasta el tamaño
  habitual de los adjuntos); **@** enlaza una página: los demás solo ven el título si pueden ver la página. Las imágenes aparecen en el
  historial como una vista previa pequeña (creada al enviar, también cifrada); el original solo se carga al tocarla. El
  navegador guarda las imágenes cargadas cifradas (hasta 200 MB; se borran al cerrar sesión). Con «⋯» en un
  mensaje: **responder**, copiar texto, **eliminar para todos** (tus mensajes). «está escribiendo…» y los mensajes nuevos
  aparecen al instante (canal en directo).
- **Menú del chat (⋯ arriba):** **número de seguridad** (60 cifras, igual en ambos lados: compáralo en persona o por
  teléfono y «Marcar como verificado»; si cambia, el chat avisa), miembros, **silenciar** (sin notificación, no cuenta
  como no leído), **eliminar mensajes automáticamente tras** 1 hora … 1 año (para mensajes nuevos), cambiar el nombre de
  un grupo libre o salir de él. La lupa busca en los mensajes cargados, solo en este dispositivo.
- **⚙ Chats y dispositivos:** dispositivos con acceso, quitar dispositivos (dejan de recibir al instante), crear una
  nueva clave de recuperación (la anterior deja de funcionar).
- Notificación «Nuevo mensaje de Nombre», sin contenido; se puede desactivar en Ajustes → Cuenta → Notificaciones.

**Administración:** General → «Chats»: **Permitir chats** (activado por defecto) y **Conservar mensajes como máximo
(días)**: el servidor elimina cada hora los mensajes y adjuntos más antiguos, aunque un chat tenga un plazo mayor. En el
usuario solo aparecen recuentos (chats, dispositivos). Eliminar una cuenta borra claves, dispositivos, pertenencias,
mensajes enviados y adjuntos; los chats directos con esa cuenta desaparecen por completo. Límites: 64 KB por mensaje,
60 mensajes por minuto, 10 dispositivos por cuenta, 50 miembros por chat, `CHAT_QUOTA_MB` (1024 por defecto) para
adjuntos por cuenta. Sin puerto adicional.

**Técnica (formato v1):** solo libsodium (navegador: libsodium.js, incluido en `static/vendor/libsodium`). Por cuenta una
clave de identidad (Ed25519) y una clave de copia de seguridad (X25519); por dispositivo X25519 + Ed25519, firmadas por
la clave de identidad. Cada mensaje: clave aleatoria, XChaCha20-Poly1305 (`crypto_aead_xchacha20poly1305_ietf`), la clave
sellada con `crypto_box_seal` para cada dispositivo y la clave de copia de seguridad de cada miembro, firmado por el
dispositivo (`crypto_sign_detached`). El paquete de claves del servidor está cifrado con una clave derivada de la clave de
recuperación mediante `crypto_pwhash` (Argon2id). Formato exacto: cabecera de `saga/chat.py`; vectores de prueba para
clientes propios: `tests/chat_vectors.json` (comprobar con `python3 tools/chat_vectors.py check` o
`node tools/chat_vectors.mjs`). En el navegador, las claves privadas están en IndexedDB; en una ventana privada los chats
no funcionan.

### Llamadas (audio, vídeo, pantalla)

En un **chat directo** hay dos botones arriba: **📞 Llamar** y **🎥 Videollamada**. Saga conecta los dos dispositivos
directamente (WebRTC): los medios no pasan por el servidor de Saga y están **cifrados de extremo a extremo**. Sin puerto
adicional: Saga coordina la llamada por el puerto web normal (canal en vivo + peticiones normales).

- **Tono de llamada:** la otra persona ve «Nombre está llamando» con Responder/Rechazar en **cualquier página de Saga**
  (panel, páginas, chats) – en videollamadas también «Responder solo con audio» –, además de un tono (🔔/🔕 lo apaga en
  este navegador) y una **notificación push** al móvil/ordenador («Llamada de Nombre», o sin nombre si «Mostrar
  nombres» está desactivado). Suenan todos los dispositivos y pestañas de la persona; al responder en uno, los demás
  dejan de sonar. Responder en el panel o en una página abre la llamada en la pestaña de chats (ahí están las claves
  de este navegador).
- **Durante la llamada:** imagen grande de la otra persona, imagen propia pequeña, **silenciar**, **cámara sí/no**
  (también después en una llamada de audio), **compartir pantalla** (en el ordenador), **colgar**; «▭» reduce la
  ventana a una esquina (para seguir escribiendo). Arriba: duración, calidad de la conexión y el camino – **directo** o
  **por relé (TURN)** – y «🔒 Cifrado de extremo a extremo» en cuanto la comprobación (abajo) ha funcionado.
- **Sin respuesta:** tras la duración del tono (por defecto 45 s) la llamada cuenta como **perdida**. En el chat
  aparece «Llamada perdida» (con «Devolver llamada») y una notificación «Llamada perdida de Nombre» (desactivable en
  notificaciones o push → «Llamadas»). Las llamadas finalizadas, rechazadas y ocupadas también aparecen en el chat.
- **Ocupado:** quien ya está en una llamada no recibe una segunda; quien llama ve «Ocupado».
- **¿Quién puede llamar?** Solo las dos personas de un chat directo, ambas con los chats configurados. Los chats
  silenciados no hacen sonar el móvil. Contra el spam: como máximo 3 llamadas por minuto a la misma persona.
- **Cámara/micrófono:** el navegador pregunta en la primera llamada. Si se denegó, Saga explica dónde volver a
  permitirlo (icono de candado junto a la dirección). Las llamadas necesitan `https`.

**Accesibilidad:** en la misma red y entre conexiones domésticas habituales, la conexión directa casi siempre funciona;
para ello los dispositivos preguntan a un **servidor STUN** por su dirección pública (por defecto:
`stun:stun.nextcloud.com:443`). Detrás de cortafuegos corporativos estrictos, en algunas redes móviles (CGNAT) o si un
lado solo puede usar el puerto 443 hacia fuera, hace falta un **servidor TURN** que reenvíe los medios (siguen
cifrados: el servidor TURN no puede escuchar). Sin él se ve «No es posible una conexión directa … El administrador
puede configurar un servidor TURN».

**Administración → General → «Llamadas»:** **Permitir llamadas** (activado por defecto; requiere los chats), **servidor
STUN** (vacío = ninguno; privacidad: el servidor STUN ve las direcciones IP de quienes llaman; si no se quiere, usar uno
propio, p. ej. el propio coturn), **servidor TURN** con **secreto compartido** (recomendado: Saga genera credenciales por
llamada según el esquema TURN REST, válidas 12 horas; el servidor TURN solo ve un hash en lugar del nombre de la cuenta)
o con **usuario/contraseña fijos**, **Conectar solo a través de TURN** (los dispositivos no ven la dirección IP de la
otra persona) y la **duración del tono** (15–120 s). Los secretos se guardan cifrados; «Borrar credenciales TURN» los
elimina.

#### TURN por el puerto 443 (coturn detrás del proxy inverso, mediante SNI)

Saga no abre ningún puerto, y TURN tampoco lo necesita: al iniciar la conexión, el proxy inverso mira el nombre en el
saludo TLS (SNI) y pasa `turn.example.org` sin cambios a coturn; todo lo demás va como antes a los servidores web. Solo
hace falta un nombre DNS más (y un certificado para él).

1. Instalar **coturn** (paquete `coturn` o contenedor `coturn/coturn`) y `/etc/turnserver.conf`:

```ini
use-auth-secret
static-auth-secret=A-LONG-RANDOM-SECRET                  # p. ej. openssl rand -hex 32 - el mismo en la administracion de Saga
realm=turn.example.org
listening-ip=127.0.0.1                                   # solo el proxy habla con coturn
tls-listening-port=5349
cert=/etc/coturn/fullchain.pem                          # certificado para turn.example.org
pkey=/etc/coturn/privkey.pem
fingerprint
no-cli
no-multicast-peers
# sin reenvio hacia la red propia (evita el abuso como tunel) ...
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
# ... salvo a las propias direcciones de rele (ambos lados por TURN): IP de este servidor
allowed-peer-ip=192.0.2.10                               # IP de ejemplo - sustituir por la propia
relay-ip=192.0.2.10
```

2. Repartir el puerto 443 por SNI en el **proxy inverso**. Los servidores HTTPS existentes escuchan entonces
   internamente (aquí 8443).

nginx (módulo `stream`, `ssl_preread`):

```nginx
stream {
    map $ssl_preread_server_name $backend {
        turn.example.org  127.0.0.1:5349;     # coturn (hace TLS el mismo)
        default           127.0.0.1:8443;     # todos los servidores https anteriores: "listen 127.0.0.1:8443 ssl;"
    }
    server {
        listen 443;
        listen [::]:443;
        ssl_preread on;
        proxy_pass $backend;
    }
}
```

HAProxy:

```
frontend tls443
    bind :443
    mode tcp
    tcp-request inspect-delay 5s
    tcp-request content accept if { req.ssl_hello_type 1 }
    use_backend coturn if { req.ssl_sni -i turn.example.org }
    default_backend web
backend coturn
    mode tcp
    server turn 127.0.0.1:5349
backend web
    mode tcp
    server web 127.0.0.1:8443
```

Traefik (router TCP con TLS passthrough, punto de entrada `websecure` = 443):

```yaml
tcp:
  routers:
    turn:
      entryPoints: [websecure]
      rule: "HostSNI(`turn.example.org`)"
      service: coturn
      tls:
        passthrough: true
  services:
    coturn:
      loadBalancer:
        servers:
          - address: "127.0.0.1:5349"
```

Caddy solo puede hacerlo con el plugin `caddy-l4` (matcher `tls sni turn.example.org` → `proxy 127.0.0.1:5349`).

3. En la **administración de Saga**: servidor TURN `turns:turn.example.org:443?transport=tcp`, acceso «Secreto
   compartido» y el mismo secreto que en coturn. Opcional: «Conectar solo a través de TURN».
4. **Comprobar:** llamada entre un dispositivo en wifi y un móvil con datos; la ventana de llamada muestra «por relé
   (TURN)». Sin llamada: `turnutils_uclient -t -S -p 443 -W <secreto> turn.example.org` (del paquete coturn).

Notas: TURN sobre TLS/TCP en el 443 atraviesa casi cualquier cortafuegos, pero añade algo de latencia. El reenvío
entre dos dispositivos ocurre dentro de coturn (ambos lados obtienen una dirección de relé), así que el rango de puertos
de relé (`min-port`/`max-port`) no tiene que ser accesible desde fuera. Si además se puede abrir UDP 3478, añadir
`turn:turn.example.org:3478` como segunda dirección (más rápido donde UDP está permitido).

**Técnica (formato `saga-call/v1`):** WebRTC con DTLS-SRTP. Cada mensaje de señalización (oferta/respuesta SDP,
candidatos ICE, estado) lo firma el **dispositivo de chat** emisor con su clave Ed25519 – sobre
`LP("saga-call/v1/signal", call_id, de_cuenta, de_dispositivo, a_cuenta, a_dispositivo, body)` – y lo sella con
`crypto_box_seal` para el dispositivo de destino. El servidor no ve ni el SDP ni las direcciones IP y no puede colar su
propia clave DTLS: el SDP con la huella DTLS está firmado, el certificado del dispositivo depende de la clave de
identidad (número de seguridad en el chat) y, tras conectar, el navegador compara otra vez la huella de la otra parte
con el SDP firmado. Si algo no coincide, la llamada se cancela con un mensaje claro. Endpoints: `/api/calls/config`,
`POST /api/calls`, `/api/calls/{id}` (`answer`, `decline`, `signal`, `end`, `alive`), `/api/calls/active`; eventos en
vivo `call.ring`, `call.signal`, `call.state`. Vectores de prueba para clientes propios: `tests/call_vectors.json`
(`python3 tools/call_vectors.py check`, `node tools/call_vectors.mjs`). Las llamadas aparecen en el chat como entrada con
metadatos (tipo, resultado, duración: solo lo que el servidor ya sabe); las apps las reciben con `?calls=1`. Push: tipo
`call` con prioridad alta y vida corta (ntfy prioridad 5); para la app de iOS/macOS además push VoIP de Apple (PushKit,
tipo de suscripción `apns-voip`, topic `<bundle-id>.voip`). El registro de accesos no contiene direcciones IP para
`/api/calls…`.

**Más adelante (llamadas de grupo):** previstas como malla de hasta unas 4 personas en un chat de grupo (cada persona
crea su propia conexión, igual de verificada, con cada una de las demás; señalización como arriba por pareja). Más allá
haría falta un servidor de reenvío (SFU) con cifrado adicional del contenido (Insertable Streams/SFrame); a propósito,
todavía no forma parte de Saga.

### Notificaciones push al móvil y al ordenador

La campana lo muestra todo mientras Saga está abierta. Para cuando no estás, Saga envía **notificaciones push**,
incluso con Saga cerrado. Actívalas en **Ajustes → Notificaciones push**:

- **Este navegador:** «Activar en este navegador» y permitir la pregunta del navegador (Chrome, Edge, Firefox,
  Safari). En **iPhone y iPad** solo funciona si Saga está en la pantalla de inicio (Compartir → «Añadir a pantalla de
  inicio»): ábrela desde allí y actívala. Requiere `https`. Al cerrar sesión en este navegador termina la suscripción;
  «cerrar sesión en todas partes» y un cambio de contraseña terminan todas las suscripciones de navegador.
- **ntfy:** en la app [ntfy](https://ntfy.sh) (Android, iPhone, Linux), suscríbete a un tema con un nombre difícil de
  adivinar e introduce su dirección en «Añadir ntfy» (p. ej. `https://ntfy.sh/mi-tema-secreto`, con token de acceso en
  temas protegidos). Los servidores ntfy propios en la red doméstica los permite el administrador en General.
- **Apps:** las apps de Android y Linux se registran mediante UnifiedPush; la app de iOS/macOS, mediante Apple Push
  (APNs, lo configura el administrador). Aparecen en la lista de dispositivos.
- **Dispositivos:** todos los navegadores, temas de ntfy y apps con «Probar» y «Eliminar»; «Último error» ayuda si no
  llega nada.
- **¿Sobre qué?** Activa o desactiva por separado compartir, comentarios, chat y publicación. Con **Mostrar nombres**
  desactivado → solo «Comentario nuevo en tu página» en lugar de «Jonas ha comentado tu página». **Horas de silencio**
  (p. ej. 22:00–07:00, en tu zona horaria): sin notificaciones push en ese tiempo; la campana lo sigue mostrando todo.

Privacidad: las notificaciones push solo contienen una frase corta y el enlace, nunca títulos de páginas, comentarios
ni contenido de chats. Las notificaciones del navegador están cifradas de extremo a extremo (Web Push, RFC 8291): el
servicio push del navegador (Google, Mozilla, Apple, Microsoft) solo ve datos cifrados. Por **ntfy** (y UnifiedPush
sin cifrado) **nunca se envían nombres**, porque el servidor ntfy puede leer el texto. Antes de entregar, Saga
comprueba que todavía puedes ver la página. Saga solo se conecta **hacia fuera**: sin puerto adicional.

**Administración → Notificaciones push:** desactivar canales por separado (Web Push, ntfy, UnifiedPush) y configurar
Apple Push: en la cuenta de Apple Developer, en *Certificates, Identifiers & Profiles → Keys*, crea una clave con
«Apple Push Notifications service (APNs)», sube el archivo `.p8` (solo se ofrece una vez) e indica Team ID, Key ID y
el bundle ID de la app. La clave se guarda cifrada. Sin clave, Apple Push queda desactivado. Saga crea por sí misma
la clave de Web Push (VAPID) cuando la necesita por primera vez; depende de `SECRET_KEY`.

### Asistente de IA (opcional)

Si el asistente está activado, la vista de páginas muestra arriba **Pregunta a Saga IA** (o el nombre que haya elegido
el administrador, p. ej. «Pregunta a Marin») con su foto de perfil. Funciona con un modelo de lenguaje que
configura el administrador (p. ej. Ollama en tu propia red). **Todo lo que le das al asistente – tu pregunta y los
fragmentos de páginas correspondientes – se envía a ese endpoint**; su dirección aparece abajo en el panel lateral.

- **Pregunta a tus páginas:** Saga busca en las páginas que *tú* puedes ver (las tuyas y las compartidas contigo; no la
  papelera ni el archivo), envía los fragmentos más adecuados y muestra la respuesta mientras se escribe. Los números
  **[1]**, **[2]** llevan a las páginas de origen; debajo aparecen todas las fuentes. Los enlaces a páginas que no puedes
  ver solo se envían como «Página sin acceso». El asistente solo incluye **tareas** y **calendario** si activas los
  interruptores bajo el campo de entrada. Nunca lee chats (cifrados de extremo a extremo) ni
  páginas de otras instancias de Saga (federación).
- **Esta página** (también en el menú de página «…» → Asistente de IA): **Resumir**, **Sugerir tareas** (marca las que
  quieras → «Añadir a tareas» o «Insertar como lista de comprobación»), **Mejorar el texto seleccionado** y
  **Traducir a …** para los bloques que hayas seleccionado. El resultado es siempre solo una **sugerencia**:
  «Reemplazar» (tras confirmar), «Insertar debajo» o «Copiar»; el asistente no puede cambiar nada por sí mismo.
  Reemplazar e insertar solo aparecen si puedes editar la página; las versiones anteriores están en el historial de
  versiones.
- **Historial:** desactivado por defecto. En «Historial» puedes activarlo y eliminar entradas sueltas o todo; al
  desactivarlo se borra el historial guardado. Las fuentes a las que ya no tienes acceso no muestran título.
- El administrador puede limitar las solicitudes por día («Quedan 12 de 50 solicitudes hoy»). Solo se ejecuta una
  solicitud por persona a la vez; **Detener** cancela una respuesta.

**Para administradores:** área de administración → **Asistente de IA**. Predeterminado: desactivado.

| Campo | Significado |
|---|---|
| Proveedor | *Compatible con OpenAI* (`/v1/chat/completions` – OpenAI, Ollama, LM Studio, vLLM …) u *Ollama* (API propia `/api/chat`) |
| Dirección | p. ej. `http://ollama.lan:11434/v1` (compatible con OpenAI) o `http://ollama.lan:11434` (Ollama). `http` solo para direcciones internas |
| Modelo | p. ej. `llama3.1:8b`; «Probar conexión» lista los modelos ofrecidos y pide una respuesta corta |
| Clave API | opcional, se guarda cifrada (como LDAP/OIDC); vacío = conservar |
| Solo para estos grupos | vacío = todas las cuentas; si no, solo los miembros (grupos locales, LDAP o SSO) |
| Tiempo límite, solicitudes por persona y día, contenido de páginas por solicitud | límites (0 solicitudes = ilimitado) |
| Permitir historial | las personas pueden activar su historial |

Saga solo abre conexiones **salientes** a exactamente esta dirección; no hay ningún puerto adicional. Se permiten
direcciones internas (Ollama en la LAN), pero solo con las direcciones IP resueltas al guardar («Direcciones fijadas»);
si la dirección cambia en el DNS, vuelve a guardar. Saga no sigue redirecciones. `AI_MAX_PARALLEL` limita las solicitudes
simultáneas en total (por defecto 4). Las preguntas, el contenido y las respuestas nunca acaban en el registro.

Para protegerse de instrucciones inyectadas («Ignora todas las reglas …» dentro de una página), Saga marca el contenido
de las páginas como datos para el modelo y no da al asistente ningún permiso de escritura. Aun así, un modelo de lenguaje
puede equivocarse o dejarse influir por el contenido: revisa las respuestas, sobre todo en páginas compartidas.

**Persona:** en el área de administración → Asistente de IA → **Persona** la IA recibe un nombre (vacío = «Saga KI» /
«Saga AI» / «Saga IA» según el idioma), una **foto de perfil** (PNG, JPEG, GIF, WebP o HEIC; se comprueba como las fotos de
perfil, se recorta en cuadrado y se vuelve a codificar; sin foto, Saga muestra un símbolo ✦ neutro) y, opcionalmente, una
breve **personalidad y tono** (como máximo 500 caracteres). El tono solo influye en la redacción de sus propias respuestas;
las reglas, las fuentes y los formatos tienen prioridad, y mejorar y traducir mantienen el tono del texto. La web y las
apps muestran el nombre y la foto allí donde aparece la IA y aplican los cambios al instante.

**IA en los chats, solo si se la menciona:** si el administrador activa «Permitir la IA en los chats» (por defecto:
desactivado), la IA puede permitirse en chats concretos (menú del chat «…» → «Permitir a Marin en este chat»: en grupos
libres, los administradores del chat; en chats directos, ambas personas; en chats de grupo, los administradores de
Saga; en chats de página, el propietario de la página). Todos los miembros ven entonces un aviso arriba en el chat.
Quien escribe **@Marin** (al teclear «@» se sugiere la IA) envía **ese mensaje y hasta 12 mensajes anteriores en texto
claro** al endpoint de IA; todo lo demás sigue cifrado de extremo a extremo. La IA no ve páginas, tareas ni
calendarios. El dispositivo de quien pregunta cifra la respuesta como cualquier mensaje para todos los miembros;
aparece con el nombre y la foto de la IA («preguntado por …»). El servidor no guarda ni la pregunta ni la respuesta,
solo una suma de comprobación con la que los demás dispositivos verifican que de verdad viene de la IA. Se aplican la
cuota y los grupos del asistente y, además, como máximo 6 preguntas por minuto.

### Cuenta, calendarios, Immich, apps

En el menú de usuario, arriba a la derecha, en **Ajustes** (una pestaña para cada cosa):

- **Cuenta:** cambiar nombre, correo y contraseña (en cuentas LDAP/SSO, en el proveedor). Al cambiar la
  contraseña se cierra la sesión en los demás navegadores **y en todas las apps**, salvo en esta sesión y en la
  app que estás usando ahora. Las apps tendrán que iniciar sesión una vez más. Aquí también se cambia el
  **nombre público** (ver «Publicar páginas»).
- **Calendarios:** conectar cuentas CalDAV (Nextcloud: `https://cloud.example.org/remote.php/dav/`).
  Lo mejor es crear una contraseña de aplicación solo para calendarios.
- **Immich:** introduce la dirección y una clave de API con los permisos `album.read`, `asset.read`,
  `asset.view`, `asset.download`, `user.read`. Así podrás usar tus fotos como imágenes y portadas.
- **Apps y dispositivos:** lista de todas las apps con sesión iniciada. Cada una lleva una pequeña clave para el
  widget, la extensión para compartir y los atajos; **Cerrar sesión** elimina ambas cosas. Las claves creadas a
  mano (p. ej. para scripts) aparecen plegadas en "Más claves".
- **Iniciar sesión con código QR:** en "Apps y dispositivos" aparece un código QR válido durante dos minutos y
  de un solo uso. Escanéalo con la cámara del móvil: la app toma la dirección del servidor y el inicio de
  sesión, sin escribir ninguna contraseña en el dispositivo. Para las apps de Mac y Linux hay debajo un botón
  **"Copiar enlace"**: pega el enlace de vinculación en la app al iniciar sesión.
- **Iniciar sesión en una app a través del navegador:** la app abre la página de inicio de sesión de Saga. Si
  ya tienes la sesión iniciada allí, Saga pregunta antes ("Iniciar sesión en la app"): confírmalo solo si acabas
  de iniciar tú mismo el inicio de sesión en la app. Si inicias sesión en ese momento en esa ventana (contraseña
  o SSO), no hay pregunta adicional. **Cancelar** te devuelve a la app.

Los servidores públicos de calendario e Immich (https) funcionan siempre. Los **servidores de la red local**
(p. ej. `192.168.1.20` o `nextcloud.lan`) tiene que permitirlos antes el administrador en General: así la red
interna no puede consultarse a través de Saga. Saga se conecta directamente a la dirección comprobada; para
calendarios e Immich **no** se usa un proxy de salida (`HTTPS_PROXY`/`HTTP_PROXY`).

![Ajustes](../screenshots/einstellungen.png)

### Llaves de acceso e inicio de sesión en dos pasos

En **Ajustes → Cuenta → Inicio de sesión y seguridad** proteges una cuenta local o LDAP además de con la contraseña
(las cuentas SSO lo hacen en su servicio de inicio de sesión, ver abajo):

- **Llaves de acceso (passkeys):** el navegador o el móvil crea una clave que se guarda en el gestor de
  contraseñas del dispositivo (Llavero de iCloud, Gestor de contraseñas de Google, 1Password, Bitwarden …) o en una
  llave de seguridad (YubiKey). Con ella inicias sesión **sin contraseña**: «Iniciar sesión con llave de acceso» en
  la página de inicio de sesión o directamente desde las sugerencias del nombre de usuario, y confirmas con huella,
  cara o PIN del dispositivo. Si inicias sesión con contraseña, la llave de acceso sirve de segundo factor. Puedes
  añadir varias (p. ej. móvil y portátil); cada una tiene nombre, «creada» y «último uso».
- **App de autenticación** (TOTP: Aegis, 2FAS, Google/Microsoft Authenticator …): escanea el código QR o escribe
  la clave y luego introduce un código. Saga pide el código de 6 cifras tras la contraseña; cada código sirve una
  sola vez.
- **Códigos de recuperación:** con el primer factor recibes 10 códigos de un solo uso; imprímelos o guárdalos en
  el gestor de contraseñas, Saga solo los muestra una vez. Te permiten entrar si te faltan el móvil o la llave de
  acceso. «Generar códigos nuevos» invalida los anteriores.

**Inicio de sesión:** contraseña correcta → Saga pide el segundo factor (llave de acceso, código o código de
recuperación). Este paso vale 5 minutos y solo en este navegador; tras 5 códigos erróneos hay una pausa de 15
minutos. A propósito no existe la opción «confiar en este navegador».

**Confirmar antes de cambios:** añadir o eliminar llaves de acceso, cambiar la app de autenticación o generar
códigos nuevos es posible durante 5 minutos tras iniciar sesión; si no, Saga pide antes la contraseña, un código o
una llave de acceso. Lo mismo vale para «Empezar de nuevo» en los chats: ahora también sirven una llave de acceso
o un código en lugar de la contraseña.

**Apps:** las apps inician sesión a través de la página de inicio de sesión de Saga en la ventana del navegador
del sistema; allí aparece también el segundo paso, y las llaves de acceso funcionan en iPhone, iPad, Mac y Android.
Las apps ya conectadas siguen conectadas aunque actives el 2FA o el administrador lo exija. El emparejamiento por
código QR no necesita segundo paso (parte de una sesión ya iniciada). Si el administrador exige 2FA a todos y tu
cuenta aún no tiene factor, un inicio de sesión **nuevo** de una app lleva primero a la configuración («Continuar»
vuelve después a la app); el código QR para emparejar solo está disponible después.

**Requisito para las llaves de acceso:** `BASE_URL` con nombre de host y `https://` (p. ej.
`https://saga.example.org`; solo `http://localhost` funciona sin https). Si Saga se abre con otra dirección que
`BASE_URL`, Saga lo explica en lugar de fallar en silencio. Las llaves de acceso pertenecen al dominio: si
`BASE_URL` pasa más tarde a otro dominio, las existentes dejan de funcionar (iniciar sesión con contraseña +
código/código de recuperación sigue funcionando). Para que valgan en varios subdominios, pon `PASSKEY_RP_ID` en el
dominio común. En el **área de administración** solo funcionan si `ADMIN_URL` está en el mismo dominio; si no, usa
allí un código o un código de recuperación. Por eso, cuando la política exige un factor, un administrador sin
factor configura allí una app de autenticación (llaves de acceso en la página principal, en «Inicio de sesión y
seguridad»).

**SSO (OIDC):** Saga no ofrece su propio 2FA a las cuentas SSO: el servicio de inicio de sesión (p. ej. Keycloak)
pide el segundo factor. Con el interruptor de administración «Exigir un segundo factor con SSO», Saga solo acepta
inicios de sesión SSO si el proveedor indica uno (`amr` con `mfa`, `otp`, `hwk` …) o uno de los valores `acr`
configurados (que Saga también solicita al proveedor).

**¿Perdiste el móvil?** Inicia sesión con un código de recuperación y vuelve a configurar el factor. Sin códigos,
el administrador restablece el segundo factor (sección 5) o, si el propio administrador no puede entrar, en el
servidor `docker compose exec saga python run.py 2fa-zuruecksetzen <nombre>` (sección 2).

### Fotos de perfil

En lugar de las iniciales de color, Saga muestra una **foto de perfil**: en la cabecera, en los comentarios, en la
presencia de una página, en las notificaciones, en el diálogo de compartir y la búsqueda de personas, en los chats
(lista, conversación, miembros) y en el historial de versiones («de …»). Sin imagen se quedan las iniciales.

- **Imagen propia:** Ajustes → Cuenta → Foto de perfil → «Subir imagen …». En el navegador eliges un recorte
  cuadrado (mover, ampliar). Se permiten PNG, JPEG, GIF, WebP y HEIC hasta `AVATAR_MAX_MB` (por defecto 5 MB). El
  servidor vuelve a codificar cada imagen (64/128/256 píxeles, WebP y PNG/JPEG) y elimina todos los metadatos
  (EXIF con cámara, lugar, fecha). «Quitar mi imagen» la borra de nuevo.
- **Gravatar por defecto:** sin imagen propia –y con una dirección de correo en la cuenta– se usa tu
  [Gravatar](https://gravatar.com). Privacidad: **solo el servidor** consulta Gravatar (con el hash SHA-256 de la
  dirección) y guarda el resultado un día, también «sin imagen». Los navegadores y las apps nunca consultan
  Gravatar directamente, así que Gravatar no sabe quién mira una imagen. Cada persona puede desactivarlo para sí
  («Usar Gravatar si no tengo imagen propia»), el admin para todos (General → Fotos de perfil).
- **Imagen del proveedor de identidad (opcional, admin):** «Usar la imagen del SSO» toma la dirección del claim
  OIDC `picture` (la obtiene el servidor, no el navegador); «Usar la imagen de LDAP» lee `jpegPhoto` o
  `thumbnailPhoto` al iniciar sesión. Saga solo obtiene direcciones internas incluidas en «Hosts internos
  permitidos» (protección contra SSRF); las imágenes obtenidas se limitan a 2 MB y pocos segundos.
- **Orden:** imagen propia → imagen del proveedor de identidad → Gravatar → iniciales.
- **¿Quién ve la imagen?** Las direcciones contienen una clave imposible de adivinar (`/api/avatar/<clave>`); solo
  las personas (o apps) con sesión iniciada pueden cargarlas y no se pueden enumerar las cuentas. En las **páginas
  publicadas** tu imagen aparece junto a «@nombre» solo si activas «Mostrar también en mis páginas publicadas»
  (por defecto: desactivado).
- **Federación:** las personas de otras instancias de Saga también muestran su imagen. Tu servidor la obtiene de
  su instancia con una petición firmada (máximo 512 KB), la vuelve a codificar y la guarda un día; al desconectar
  la instancia se borra la caché.
- **Borrar una cuenta** elimina su imagen, sus ajustes y todas las imágenes guardadas.

### Idioma

Saga habla español, inglés y alemán. El idioma que ves se decide en este orden:

1. tu propia elección en **Ajustes** → **Cuenta** → **Idioma**: se aplica al instante y en cualquier navegador,
   porque se guarda en tu cuenta,
2. el **idioma predeterminado** que el administrador fija en General,
3. el idioma de tu navegador,
4. si no, inglés (también para cualquier otro idioma del navegador).

"Automático" significa siempre: decide el paso siguiente. Las fechas y los números siguen el idioma elegido. Las
apps siguen el idioma del dispositivo.

### En el móvil

Saga se adapta a pantallas pequeñas y se puede añadir a la pantalla de inicio como una app ("Añadir a pantalla
de inicio" en el menú del navegador). En las páginas, el contenido aparece arriba del todo; el árbol de páginas
se abre como un cajón con el botón de arriba a la izquierda. `/` abre el menú de bloques como una hoja desde
abajo: toca una entrada, o sigue escribiendo (filtra) y pulsa `Enter`. Las barras de pestañas (ajustes, área de
administración) se pueden deslizar de lado.

<img src="../screenshots/handy.png" width="320" alt="Saga en el móvil">

## 10. Búsqueda web (opcional)

El campo de búsqueda también puede buscar en la web, a través de una instancia propia de SearXNG a la que solo
accede Saga:

```sh
# en el .env
COMPOSE_PROFILES=search
SEARXNG_URL=http://searxng:8080
SEARXNG_SECRET=<openssl rand -hex 32>
```

Después, `docker compose up -d`. Las búsquedas solo van a tu servidor, no directamente a los buscadores.

## 11. Copias de seguridad, restauración y actualizaciones

**Qué hay que guardar:**

1. la base de datos,
2. el volumen `saga-data` (imágenes y archivos de las páginas),
3. el archivo `.env` (sobre todo la `SECRET_KEY`).

```sh
# copia de la base de datos
docker compose exec -T db pg_dump -U saga -d saga -Fc > saga-$(date +%F).dump
# copia de los archivos
docker run --rm -v saga_saga-data:/data -v "$PWD":/backup alpine tar czf /backup/saga-data-$(date +%F).tgz -C /data .
```

```sh
# restaurar (instalación vacía, misma SECRET_KEY)
docker compose up -d db
docker compose exec -T db pg_restore -U saga -d saga --clean --if-exists < saga-2026-10-13.dump
docker run --rm -v saga_saga-data:/data -v "$PWD":/backup alpine tar xzf /backup/saga-data-2026-10-13.tgz -C /data
docker compose up -d
```

(El nombre del volumen empieza por el nombre de la carpeta del proyecto: `docker volume ls` lo muestra.)

### Actualizaciones

Saga usa números de versión según [Semantic Versioning](https://semver.org/lang/es/): **1.2.3** =
mayor.menor.parche. Los cambios están en el [registro de cambios](../../CHANGELOG.md) (en alemán) y en las
[versiones publicadas](https://github.com/yggdrasil-sh/saga/releases). La versión instalada aparece abajo en el
área de administración; ahí también verás un aviso cuando haya una nueva (se puede desactivar en General).

Qué versión se ejecuta lo decide `SAGA_VERSION` en el `.env`:

| Valor | Significado |
|---|---|
| `2` (por defecto) | siempre la 2.x más reciente: funciones nuevas y correcciones, nada que requiera trabajo manual |
| `2.1` | solo correcciones de la 2.1 |
| `2.1.0` | exactamente esta versión |
| `1` | se queda en la 1.x más reciente (1.2); las instalaciones antiguas aún tienen este valor en el `.env` |
| `testing` | **Versión de pruebas:** lo último de la rama `testing`, antes de publicarse. Puede tener errores; solo para probar |

**Actualizar:**

```sh
git pull                                  # docker-compose.yml y guía al día
docker compose pull                       # imagen nueva
docker compose up -d                      # reiniciar
docker compose logs saga | grep saga:     # copia de seguridad y migración de la base de datos en el log
```

Si una actualización cambia la base de datos, Saga **crea antes una copia de seguridad automáticamente** en
`/data/backups` (en el volumen `saga-data`; se conservan las 10 últimas, ver `BACKUP_KEEP` en el `.env`).
Una copia a mano:

```sh
docker compose exec saga python run.py backup              # solo la base de datos
docker compose exec saga python run.py backup --dateien    # además, imágenes/archivos de las páginas como tar.gz
docker compose exec saga ls -l /data/backups
```

Las imágenes y archivos de las páginas (`/data/files`) **no** están en la copia de la base de datos. `--dateien`
(o `--files`) crea además `saga-dateien-<fecha>.tar.gz` (sin miniaturas, se vuelven a generar); se restaura con
`docker compose exec saga tar -xzf /data/backups/<archivo> -C /data`. Si ya haces copia del volumen `saga-data`
(ver arriba), no necesitas la opción.

**Versión de pruebas:** con `SAGA_VERSION=testing` recibes las funciones nuevas en cuanto están listas, antes
de publicarse. La versión se llama entonces, por ejemplo, `2.2.0-testing.ab12cd3` (abajo en el área de
administración, con 🧪). Para volver a la versión estable: `SAGA_VERSION=2` y después
`docker compose pull && docker compose up -d`. Si la versión de pruebas ya ha modificado la base de datos, la
versión estable solo arrancará cuando incluya también ese cambio (como muy tarde con la siguiente versión), o
bien restauras la copia de seguridad de antes del cambio.

Una nueva **versión mayor** puede requerir trabajo manual; lo indicará el registro de cambios y solo llega si
pones `SAGA_VERSION` en la nueva versión mayor.

**De 1.x a 2.0:** si tu `.env` todavía dice `SAGA_VERSION=1`, te quedas en la 1.2. Para Saga 2.0 pon
`SAGA_VERSION=2` y actualiza como arriba: la base de datos se adapta sola (antes se hace una copia de seguridad).
Después, si los quieres, configura en el área de administración el asistente de IA, la federación y las
notificaciones de Apple (APNs) (están desactivados) y revisa el proxy inverso para el canal en vivo (sección 3).
Todo lo demás está en el [registro de cambios](../../CHANGELOG.md).

**De 2.0 a 2.1:** con `SAGA_VERSION=2`, la 2.1 llega con la actualización normal. La base de datos pasa al
esquema 6 (antes copia de seguridad; después no se puede volver a 2.0.x salvo con esa copia). Nuevo y activo desde
el principio: fotos de perfil con **Gravatar** (se desactiva en General → Fotos de perfil) y **llamadas** con el
servidor STUN `stun:stun.nextcloud.com:443` (General → Llamadas; TURN opcional, ver «Llamadas»). Las passkeys
necesitan un nombre de host en `BASE_URL`, no una dirección IP; la política de 2FA está en «nadie».

**Volver a la versión anterior:** una versión antigua de Saga no arranca sobre una base de datos que una versión
más nueva ya ha modificado (lo indica en el log en lugar de dañar datos). Esto vale también para **2.1 → 2.0.x**:
2.1 sube el esquema a la versión 6 (copia `…-vor-schema-6.dump`) y 2.0.x se detiene con «Die Datenbank hat
Schema-Stand 6, diese Saga-Version kennt nur 5»; si no, 2.0 seguiría funcionando y dejaría entrar a cuentas con
segundo factor solo con la contraseña. En ese caso, restaura la copia de seguridad de antes de la actualización:

```sh
docker compose stop saga
docker compose run --rm saga ls /data/backups                     # p. ej. saga-20261013-183000-vor-schema-3.dump
docker compose run --rm saga python run.py restore /data/backups/saga-20261013-183000-vor-schema-3.dump
# poner en el .env SAGA_VERSION a la versión antigua (p. ej. 1.2.3) y después:
docker compose pull && docker compose up -d
```

Todo lo que se haya cambiado después de la copia se pierde, así que hazlo solo poco después de una
actualización fallida.

**Apps:** al arrancar, las apps comprueban la versión del servidor. Si una app es demasiado antigua para el
servidor, te pide que la actualices. En los ajustes de la app aparecen la versión de la app y la del servidor.

## 12. Solución de problemas

**Después de iniciar sesión vuelvo a la página de inicio de sesión.**
`BASE_URL` debe ser exactamente la dirección que aparece en el navegador (incluido `https://`). Con `https`,
Saga solo envía cookies por HTTPS.

**"Demasiados intentos fallidos".**
Tras 8 contraseñas incorrectas por cuenta (30 por dirección) en 15 minutos, Saga te hace esperar. Detrás de un
proxy inverso, configura `TRUSTED_PROXIES`; si no, todos los visitantes comparten la dirección del proxy.

**Llave de acceso: «El navegador indica la dirección …, pero Saga está configurado para …».**
Saga se abrió con otra dirección que `BASE_URL` (p. ej. una IP o un nombre antiguo). Las llaves de acceso están
ligadas a la dirección: abre Saga a través de `BASE_URL` o corrige `BASE_URL`. «Las llaves de acceso necesitan una
dirección con nombre de host»: `BASE_URL` contiene una IP o no empieza por `https://`.

**Faltan los iconos de los servicios.**
Saga los descarga una vez de `cdn.jsdelivr.net` (selfh.st) y los guarda. Sin internet aparece la inicial. Con
`ICON_CDN=` (vacío), Saga no descarga nada de internet.

**LDAP: falla el inicio de sesión de la cuenta de servicio, o errores de certificado.**
Compruébalo con "Probar conexión". Con una CA propia, pon el archivo en `certs/` (sección 7).

**SSO: falla el inicio de sesión SSO.**
Revisa la URI de redirección en el proveedor (`BASE_URL/auth/callback`) y comprueba el documento de
descubrimiento con "Probar conexión". Los detalles están en `docker compose logs saga`.

**Calendario: el servidor no es accesible o no está permitido.**
Los servidores internos deben estar permitidos en Administración → General. Los calendarios e Immich no pasan por
`HTTPS_PROXY`/`HTTP_PROXY` (para que la comprobación no se pueda saltar a través del proxy): Saga necesita una
conexión directa con esos servidores.

**"Hay demasiados inicios de sesión ahora mismo. Inténtalo de nuevo en un momento."**
Saga comprueba como mucho `KDF_PARALLEL` contraseñas a la vez (por defecto 4, unos 32 MB de memoria cada una) y
espera como mucho 2 segundos a que quede un hueco libre. Si pasa a menudo (muchos usuarios, servidor potente),
sube `KDF_PARALLEL` en el `.env`.

**Llamada: «No es posible una conexión directa», o suena pero no conecta.**
Un lado está detrás de un cortafuegos estricto o de CGNAT móvil. Configurar un servidor TURN (sección «Llamadas» →
«TURN por el puerto 443»). Si la ventana de llamada muestra «por relé (TURN)», TURN funciona. «Cámara/micrófono no
permitidos»: permitirlo en el navegador con el icono de candado junto a la dirección; las llamadas necesitan `https`.
Si no suena en otras páginas, comprobar que el canal en vivo atraviesa el proxy (sección 3).

**Subir o guardar falla con "demasiado grande" (413).**
Archivos: `ATTACH_MAX_MB` (por defecto 10). Todo lo demás, p. ej. páginas muy largas: `REQUEST_MAX_MB` (por
defecto 2). Si el mensaje viene del proxy inverso, sube allí el límite (sección 3).

**Después de cambiar la contraseña tengo que volver a iniciar sesión en las apps.**
Es lo previsto: una contraseña nueva cierra la sesión en todos los demás navegadores y apps (p. ej. por si se
perdió un dispositivo). Solo siguen con la sesión iniciada la sesión y la app en las que cambiaste la contraseña.

**La campana solo se actualiza al cabo de un minuto y las páginas abiertas no se actualizan al instante.**
Entonces el canal en directo no llega y Saga consulta cada 60 segundos. Lo habitual es que el proxy inverso use búfer
con la respuesta de `/api/events` o la cierre al poco tiempo; los ajustes están en la sección 3. Más de cinco pestañas
abiertas con la misma cuenta también pasan las más antiguas a consulta periódica.

## 13. Todos los ajustes del .env

| Variable | Por defecto | Significado |
|---|---|---|
| `BASE_URL` | – (obligatorio) | Dirección pública de Saga |
| `SECRET_KEY` | – (obligatorio) | Al menos 32 caracteres, no cambiarla nunca |
| `POSTGRES_PASSWORD` | – (obligatorio) | Contraseña de la base de datos |
| `POSTGRES_USER` / `POSTGRES_DB` | `saga` | Usuario y nombre de la base de datos |
| `ADMIN_URL` | – | Enlace "Área de administración" en el menú de usuario de los administradores |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / generada | Primera cuenta de administración |
| `HOST_BIND` / `HOST_PORT` | `0.0.0.0` / `8080` | Dónde escucha Saga en el servidor |
| `ADMIN_BIND` / `ADMIN_HOST_PORT` | `127.0.0.1` / `8081` | Dónde escucha el área de administración |
| `TRUSTED_PROXIES` | `127.0.0.1` | IP del proxy inverso (`*` = todas) |
| `CANONICAL_REDIRECT` | `true` | Redirigir otros nombres de host a `BASE_URL` |
| `PUBLIC_HOSTS` | – | Dominios propios solo para páginas públicas (separados por comas, sección 9 «Publicar páginas») |
| `PUBLIC_RATE` | `120` | Solicitudes a páginas públicas por dirección IP y minuto (imágenes: cinco veces más) |
| `SESSION_HOURS` | `12` | Duración de una sesión del navegador (las apps siguen con la sesión iniciada) |
| `PASSKEY_RP_ID` | nombre de host de `BASE_URL` | Dominio de las llaves de acceso; solo si deben valer para un dominio superior (p. ej. `example.org`) |
| `SAGA_VERSION` | `2` | Qué versión descarga `docker compose pull`: `2`, `2.1`, `2.1.0` o `testing` (sección 11) |
| `BACKUP_KEEP` | `10` | Cuántas copias automáticas/manuales se conservan en `/data/backups` |
| `SAGA_UPDATE_CHECK` | `1` | `0` = no preguntar nunca a GitHub por versiones nuevas (también desactivable en el área de administración) |
| `ADMIN_SESSION_HOURS` | `4` | Duración de una sesión en el área de administración |
| `TIMEZONE` | `Europe/Berlin` | Zona horaria para exportaciones |
| `ATTACH_MAX_MB` / `ATTACH_QUOTA_MB` | `10` / `500` | Tamaño por archivo / espacio por usuario |
| `AVATAR_MAX_MB` | `5` | Archivo más grande al subir una foto de perfil |
| `CHAT_QUOTA_MB` | `1024` | Espacio para adjuntos de chat por usuario (cifrados, en `STATE_DIR/chat`) |
| `REQUEST_MAX_MB` | `2` | Petición más grande salvo subidas de archivos (p. ej. páginas muy largas); las mayores reciben 413 |
| `KDF_PARALLEL` | `4` | Comprobaciones de contraseña a la vez (unos 32 MB de memoria cada una); las demás esperan hasta 2 s |
| `NOTE_MAX_COUNT` | `5000` | Páginas por usuario |
| `IMMICH_URL` | – | Sugerencia en el diálogo de Immich (se permite automáticamente) |
| `ICON_CDN` | selfh.st vía jsDelivr | Origen de los iconos de servicios; vacío = ninguno |
| `STATUS_INTERVAL` | `60` | Segundos entre comprobaciones de estado |
| `CACHE_TTL` | `60` | Segundos que se guardan en caché los datos de cuentas y servicios |
| `LIVE_PING` | `25` | Segundos hasta la línea de latido del canal en directo (mantiene abiertos los proxies) |
| `LIVE_MAX_PER_USER` / `LIVE_MAX_TOTAL` | `5` / `2000` | Conexiones en directo por cuenta (se cierra la más antigua) / en total |
| `LIVE_MAX_AGE` | `3600` | Tras estos segundos el navegador vuelve a abrir la conexión en directo (se comprueba de nuevo la sesión) |
| `FED_ALLOW_HTTP` | – | `1` = federación también por http (solo para pruebas; si no, solo https) |
| `FED_RATE` / `FED_TIMEOUT` | `600` / `10` | Solicitudes por minuto e instancia conectada / segundos hasta el tiempo de espera |
| `SEARXNG_URL` / `SEARXNG_SECRET` | – | Búsqueda web (sección 10) |
| `SEARCH_LANGUAGE` / `SEARCH_SAFESEARCH` | `de-DE` / `0` | Idioma y filtro de la búsqueda web |
| `LOG_LEVEL` | `info` | Nivel de detalle del log |
| `DATABASE_URL` | de `POSTGRES_*` | Conexión propia a la base de datos (en lugar del contenedor `db`) |
