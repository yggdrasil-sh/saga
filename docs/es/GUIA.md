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

![Página](../screenshots/seite.png)

Las **bases de datos** reúnen entradas con propiedades (estado, fecha, número, selección, relación, fórmula…)
y las muestran como tabla, tablero, lista, galería o calendario, con filtros, orden y totales. En una tabla,
`Enter` en el nombre pasa a la fila siguiente; en la última crea directamente la próxima entrada.

![Base de datos como tablero](../screenshots/board.png)

### Publicar páginas

Cualquier página puede hacerse **pública**: se lee sin iniciar sesión, con un diseño sencillo, claro u oscuro según
el dispositivo y cómoda de leer en el móvil. En el menú de la página (**⋯** a la derecha, encima de la página, junto
a «Guardado») elige **«Publicar …»** y marca **«Pública»** en el diálogo: desde ese momento la página está disponible
en su dirección. Los cambios en las opciones se aplican al instante; solo una dirección nueva se aplica expresamente con «Cambiar
dirección».

- **Nombre público:** La primera vez eliges un nombre público (sugerido a partir de tu nombre visible, 2–32
  caracteres `a–z 0–9 - _`). Aparece en todas las direcciones y es visible para todos; **nunca** es tu nombre de
  usuario. Se cambia en Ajustes → Cuenta, en su propio bloque «Nombre público» (con su propio botón «Guardar nombre»; Saga
  comprueba mientras escribes si el nombre es válido y está libre); después, los enlaces antiguos dejan de funcionar.
  Nombres como `admin`, `api` o `login` están reservados.
- **Dirección:** `https://saga.example.org/@nombre/direccion-de-la-pagina`. La dirección se sugiere a partir del
  título y se puede cambiar con **«Cambiar dirección»** (2–60 caracteres), tras confirmar, porque el enlace actual
  deja de funcionar. Saga redirige las mayúsculas o una «/» final a la dirección correcta.
- **Publicar también las subpáginas:** Todas las subpáginas (y las entradas de bases de datos) bajo esta página
  también serán legibles, en `/@nombre/direccion-de-la-pagina/<número>`. Sin esta opción siguen siendo privadas. Esas subpáginas muestran
  «🌐 Pública (a través de «página superior»)» en la cabecera; su diálogo lo explica y lleva a la página superior.
- **Permitir buscadores:** Desactivado por defecto: la página pide entonces a los buscadores que no la indexen.
- **Como página de perfil:** Esta página aparece en `/@nombre` (como máximo una por cuenta). Sin página de perfil,
  `/@nombre` muestra una lista sencilla de tus páginas públicas. Si otra página ya es tu página de perfil, el
  diálogo la nombra: la nueva la sustituye.
- **Copiar enlace / Abrir** y **Dejar de publicar**. Las páginas publicadas muestran «🌐 Pública» en la cabecera;
  un clic abre el diálogo y «↗», a su lado, la página pública.
- **Al dejar de publicar se guarda todo:** la dirección y los ajustes se conservan (la dirección queda reservada para
  esta página). Si vuelves a publicarla, usa la misma dirección que antes.

**Qué se publica y qué no:** título, icono, portada y contenido (texto, encabezados, listas, listas de comprobación,
citas, código, imágenes, tablas, columnas, avisos, desplegables). Las bases de datos aparecen como una tabla sencilla
(título y propiedades visibles de la vista). Las inserciones se convierten en enlaces. **No** se publican los bloques
de tareas, calendario e Immich, los botones ni los archivos adjuntos. Los enlaces a otras páginas solo siguen siendo
enlaces si la página de destino también es pública; si no, queda solo el texto. Saga solo entrega imágenes que se
usan exactamente en esta página.

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
| `1` (por defecto) | siempre la 1.x más reciente: funciones nuevas y correcciones, nada que requiera trabajo manual |
| `1.2` | solo correcciones de la 1.2 |
| `1.2.3` | exactamente esta versión |
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
de publicarse. La versión se llama entonces, por ejemplo, `1.0.0-testing.ab12cd3` (abajo en el área de
administración, con 🧪). Para volver a la versión estable: `SAGA_VERSION=1` y después
`docker compose pull && docker compose up -d`. Si la versión de pruebas ya ha modificado la base de datos, la
versión estable solo arrancará cuando incluya también ese cambio (como muy tarde con la siguiente versión), o
bien restauras la copia de seguridad de antes del cambio.

Una nueva **versión mayor** (2.0.0) puede requerir trabajo manual; lo indicará el registro de cambios. Para
usarla, pon `SAGA_VERSION=2`.

**Volver a la versión anterior:** una versión antigua de Saga no arranca sobre una base de datos que una versión
más nueva ya ha modificado (lo indica en el log en lugar de dañar datos). En ese caso, restaura la copia de
seguridad de antes de la actualización:

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

**Subir o guardar falla con "demasiado grande" (413).**
Archivos: `ATTACH_MAX_MB` (por defecto 10). Todo lo demás, p. ej. páginas muy largas: `REQUEST_MAX_MB` (por
defecto 2). Si el mensaje viene del proxy inverso, sube allí el límite (sección 3).

**Después de cambiar la contraseña tengo que volver a iniciar sesión en las apps.**
Es lo previsto: una contraseña nueva cierra la sesión en todos los demás navegadores y apps (p. ej. por si se
perdió un dispositivo). Solo siguen con la sesión iniciada la sesión y la app en las que cambiaste la contraseña.

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
| `SAGA_VERSION` | `1` | Qué versión descarga `docker compose pull`: `1`, `1.2` o `1.2.3` (sección 11) |
| `BACKUP_KEEP` | `10` | Cuántas copias automáticas/manuales se conservan en `/data/backups` |
| `SAGA_UPDATE_CHECK` | `1` | `0` = no preguntar nunca a GitHub por versiones nuevas (también desactivable en el área de administración) |
| `ADMIN_SESSION_HOURS` | `4` | Duración de una sesión en el área de administración |
| `TIMEZONE` | `Europe/Berlin` | Zona horaria para exportaciones |
| `ATTACH_MAX_MB` / `ATTACH_QUOTA_MB` | `10` / `500` | Tamaño por archivo / espacio por usuario |
| `REQUEST_MAX_MB` | `2` | Petición más grande salvo subidas de archivos (p. ej. páginas muy largas); las mayores reciben 413 |
| `KDF_PARALLEL` | `4` | Comprobaciones de contraseña a la vez (unos 32 MB de memoria cada una); las demás esperan hasta 2 s |
| `NOTE_MAX_COUNT` | `5000` | Páginas por usuario |
| `IMMICH_URL` | – | Sugerencia en el diálogo de Immich (se permite automáticamente) |
| `ICON_CDN` | selfh.st vía jsDelivr | Origen de los iconos de servicios; vacío = ninguno |
| `STATUS_INTERVAL` | `60` | Segundos entre comprobaciones de estado |
| `CACHE_TTL` | `60` | Segundos que se guardan en caché los datos de cuentas y servicios |
| `SEARXNG_URL` / `SEARXNG_SECRET` | – | Búsqueda web (sección 10) |
| `SEARCH_LANGUAGE` / `SEARCH_SAFESEARCH` | `de-DE` / `0` | Idioma y filtro de la búsqueda web |
| `LOG_LEVEL` | `info` | Nivel de detalle del log |
| `DATABASE_URL` | de `POSTGRES_*` | Conexión propia a la base de datos (en lugar del contenedor `db`) |
