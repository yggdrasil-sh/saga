<p align="center">
  <img src="static/icon.svg" width="96" height="96" alt="Saga">
</p>

<h1 align="center">Saga</h1>

<p align="center">
  <b>Tu casa autoalojada para servicios, notas y tareas.</b><br>
  Panel · Páginas y bases de datos al estilo Notion · Tareas · Calendario · Marcadores · Multiusuario
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.de.md">Deutsch</a> · <b>Español</b>
</p>

<p align="center">
  <a href="https://github.com/yggdrasil-sh/saga/releases"><img src="https://img.shields.io/github/v/release/yggdrasil-sh/saga?label=versi%C3%B3n" alt="Versión"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/licencia-AGPL--3.0-blue" alt="Licencia AGPL-3.0"></a>
</p>

<p align="center">
  <a href="https://yggdrasil-sh.github.io/saga/es/">Sitio web</a> ·
  <a href="docs/es/GUIA.md">Guía</a> ·
  <a href="#inicio-rápido">Inicio rápido</a> ·
  <a href="#funciones">Funciones</a> ·
  <a href="#capturas">Capturas</a>
</p>

![Panel de Saga](docs/screenshots/dashboard.png)

Saga es una página de inicio y una base de conocimiento autoalojada para familias, equipos y homelabs: un panel
con todos tus servicios, páginas y bases de datos al estilo Notion, tareas, calendarios y marcadores. Cada
persona tiene su propio espacio privado, sin nube ni suscripciones, en tu propio servidor.

> **Novedades de Saga 2.3:** mucho más rápido con muchas páginas y bases de datos grandes (p. ej. el árbol de páginas
> se recarga en 0,05 s en lugar de 1 s con 60.000 entradas, una base de datos de 2.000 filas se puede usar tras 0,6 s
> en lugar de 12 s, 80–90 % menos datos gracias a la compresión), **federación completa** – encontrar por nombre a
> personas de servidores conectados, chats y llamadas cifrados de extremo a extremo entre servidores, editar juntos
> páginas compartidas con bases de datos, subpáginas y subidas –, **copias de seguridad cifradas** en S3, una carpeta o
> Nextcloud/WebDAV, además de **copias personales** para cada persona, y **archivos de Nextcloud en las páginas**.
> Desde la 2.1: llaves de acceso e inicio de sesión en dos pasos, fotos de perfil, llamadas de audio y vídeo, asistente
> de IA con nombre propio y búsqueda web, cinco idiomas. Todos los detalles en el
> [registro de cambios](CHANGELOG.md) (en alemán).

## Funciones

- **Panel** con mosaicos para todos los servicios, puntos de estado, favoritos, búsqueda (opcionalmente búsqueda
  web con SearXNG) y atajos de teclado
- **Servicios por grupo:** quien está en el grupo `s_nextcloud` ve automáticamente el mosaico "Nextcloud", da
  igual si el grupo es local o viene de LDAP/SSO
- **Marcadores propios** para cada usuario, visibles solo para él
- **Páginas al estilo Notion:** bloques, subpáginas, plantillas, versiones, papelera, exportación a Markdown
- **Bases de datos** con vistas de tabla, tablero, lista, galería y calendario, filtros, relaciones y fórmulas
- **Publicar páginas:** comparte una página (si quieres, con subpáginas) en `/@nombre/pagina`, legible sin cuenta,
  sin JavaScript y, opcionalmente, como página de inicio para visitantes
- **Compartir y comentar:** comparte páginas y bases de datos con personas o grupos (locales, LDAP, SSO): roles Ver,
  Comentar, Editar, las subpáginas heredan el acceso; comentarios discretos en los bloques con «Resuelto»
- **Chats cifrados de extremo a extremo:** chats directos, de grupo y de página con adjuntos, respuestas y enlaces @ a
  páginas; el servidor (y el administrador) solo ve datos cifrados: libsodium, clave de recuperación, números de
  seguridad, varios dispositivos; archivar o eliminar chats (solo para mí o para todos)
- **Llamadas de audio y vídeo:** desde el chat directo, con pantalla compartida – WebRTC entre los dispositivos, cifradas
  de extremo a extremo y verificadas con las claves del chat, sin puerto adicional (TURN opcional por el 443)
- **Notificaciones y push:** campana con actualización en directo; si quieres, al móvil/escritorio por Web Push (cifrado
  de extremo a extremo, también iPhone como app en la pantalla de inicio), ntfy/UnifiedPush o Apple Push, sin contenido
- **Asistente de IA (opcional):** pregunta a tus páginas, resume, sugiere tareas, mejora o traduce texto, con el modelo
  que elijas (p. ej. Ollama en tu propia red). Solo contenido que puedes ver, fuentes enlazadas, solo sugerencias, desactivado por defecto.
  Con nombre, imagen y tono propios; opcionalmente **en el chat solo cuando se la menciona** (@nombre, todo lo demás
  sigue cifrado) y con **búsqueda web** a través de tu propia instancia de SearXNG (fuentes enlazadas), ambas desactivadas por defecto
- **Federación:** conecta servidores Saga: comparte páginas con personas y grupos de allí (también bases de datos,
  subpáginas, subidas, versiones, exportación y presencia), encuentra personas por nombre en la libreta de direcciones
  compartida (cada cuenta puede excluirse), chats directos, de grupo y de página cifrados de extremo a extremo y
  llamadas entre servidores, con solicitudes de mensaje y bloqueo. El contenido se queda con su propietario, cada
  solicitud entre servidores va firmada (Ed25519, HTTP Message Signatures) y un servidor que se comporta mal queda
  contenido (comprobaciones, límites por servidor, registro de auditoría)
- **Copias de seguridad cifradas:** a diario base de datos, archivos y claves en **S3** (MinIO, Garage, Backblaze B2,
  Hetzner, AWS…), una **carpeta** (montaje NAS) o **Nextcloud/WebDAV**, cifradas con [age](https://age-encryption.org)
  y tu clave pública (Saga nunca conoce la privada); retención 7/4/6, verificación, avisos, restauración de prueba y
  restauración incluso sin Saga
- **Copias personales:** cada persona guarda su propio espacio en su propio Nextcloud o WebDAV, con su propia clave:
  formato abierto (ZIP cifrado con Markdown y JSON), restaurable en la propia cuenta
- **Archivos de Nextcloud en las páginas:** vincula tu propio Nextcloud e inserta imágenes, archivos y carpetas como
  bloques; quien ve la página ve también el archivo, sin cuenta de Nextcloud; o tómalo como copia
- **Rápido también con muchos datos:** respuestas comprimidas, árbol de páginas sincronizado solo con cambios, las
  tablas largas solo muestran las filas visibles, búsqueda en el servidor con índice de trigramas, medido con una
  prueba de carga repetible (`tools/perf`)
- **Tareas** con fecha límite, prioridad y repetición
- **Calendarios (CalDAV):** ver y editar Nextcloud, mailcow/SOGo, Radicale, iCloud…
- **Immich:** usa tus propias fotos como imágenes y portadas
- **Cada usuario gestiona su propio espacio:** calendarios, Immich, marcadores, claves de app; todo privado,
  ni los administradores ven el contenido
- **Cuentas:** usuarios locales, registro activable, **LDAP/Active Directory** y **SSO/OIDC**
  (Keycloak, Authentik, Authelia, Zitadel, Entra ID…)
- **Llaves de acceso e inicio de sesión en dos pasos:** llaves de acceso (también sin contraseña), app de
  autenticación (TOTP) y códigos de recuperación; el administrador puede exigir 2FA a administradores o a todos
- **Área de administración en un puerto propio** para usuarios, grupos, servicios e inicio de sesión
- **Fotos de perfil:** imagen propia (recortada, recodificada sin metadatos) o Gravatar: la obtiene y guarda el
  servidor, los navegadores nunca consultan Gravatar; opcionalmente desde SSO/LDAP
- **Español, inglés, alemán, ruso y tailandés:** cada usuario puede elegir su idioma en sus ajustes
  y, en la página de inicio de sesión, incluso antes de entrar
  (ruso y tailandés con traducción automática; se agradecen mejoras de hablantes nativos)
- **[Apps](https://github.com/yggdrasil-sh/saga-apps/blob/main/README.es.md)** (iOS/iPadOS/macOS, Linux/GTK, Android) con la misma
  API, sesión permanente y vinculación con código QR

## Inicio rápido

Necesitas un servidor con Docker y Docker Compose.

```sh
git clone https://github.com/yggdrasil-sh/saga.git
cd saga
cp .env.example .env
nano .env        # rellenar BASE_URL, SECRET_KEY, POSTGRES_PASSWORD (ver los comentarios)
docker compose pull && docker compose up -d
```

**Actualizar:** `git pull && docker compose pull && docker compose up -d`. Antes de cualquier cambio en la base
de datos, Saga crea automáticamente una copia de seguridad. De 1.x a 2.x: antes pon `SAGA_VERSION=2` en el `.env`. La 2.3 actualiza la base de datos (esquema 9; volver a 2.1
solo con la copia automática, ver la guía, sección 11). Las novedades están en el
**[registro de cambios](CHANGELOG.md)** (en alemán) y en las
[versiones publicadas](https://github.com/yggdrasil-sh/saga/releases). ¿Quieres lo último?
`SAGA_VERSION=testing` en el `.env` descarga la versión de pruebas de la rama `testing`. Las imágenes y archivos
de las páginas no están en la copia de la base de datos: haz copia del volumen `saga-data` o ejecuta
`docker compose exec saga python run.py backup --dateien`.

- **Saga:** `http://<servidor>:8080`; en producción, ponlo detrás de un proxy inverso con HTTPS (con un límite
  de unos 12 MB por petición, ver la guía)
- **Área de administración:** `http://127.0.0.1:8081` en el servidor (por defecto solo accesible en local,
  p. ej. con un túnel SSH: `ssh -L 8081:127.0.0.1:8081 servidor`)
- **Primera cuenta de administración:** `admin`, con la contraseña de `ADMIN_PASSWORD`; si no, aparece una
  generada en `docker compose logs saga`. ¿La has olvidado? `docker compose exec saga python run.py admin-passwort`

Todo lo demás (proxy inverso, usuarios, servicios, LDAP, SSO, calendarios, copias de seguridad,
actualizaciones) está en la **[guía](docs/es/GUIA.md)**.

## Capturas

| | |
|---|---|
| ![Página](docs/screenshots/seite.png) | ![Base de datos como tablero](docs/screenshots/board.png) |
| **Páginas** con bloques, listas de verificación, tablas | **Bases de datos** como tabla, tablero, calendario… |
| ![Administración: servicios](docs/screenshots/admin-dienste.png) | ![Administración: SSO](docs/screenshots/admin-sso.png) |
| **Administración:** servicios con acceso por grupos | **Administración:** SSO/OIDC con prueba de conexión |
| ![Tema claro](docs/screenshots/dashboard-hell.png) | ![Ajustes](docs/screenshots/einstellungen.png) |
| Tema claro (sigue al sistema) | Ajustes propios de cada usuario |

## Arquitectura

```
Navegador / apps ──► Saga (Python, FastAPI)  :8080  panel, páginas, API
                     Área de administración  :8081  inicio de sesión propio, solo admins
                          │
                          ├── PostgreSQL     cuentas, páginas, tareas, ajustes
                          ├── /data          imágenes y archivos de las páginas
                          └── opcional: LDAP/AD, proveedor OIDC, SearXNG, CalDAV, Immich
```

## Seguridad en resumen

- Contraseñas con scrypt, bloqueo tras varios intentos fallidos, protección CSRF, Content Security Policy
  estricta
- Credenciales de calendarios, Immich, LDAP y SSO cifradas en la base de datos (clave: `SECRET_KEY`)
- Copias de seguridad cifradas con claves públicas (age): el servidor nunca tiene la clave privada
- Datos de usuarios estrictamente separados, con pruebas automáticas (`tests/isolation.py`)
- Direcciones internas (red local) para calendarios/Immich solo con permiso del administrador (protección SSRF)
- Bloquear una cuenta cierra al instante todas sus sesiones e inicios de sesión de apps

¿Has encontrado una vulnerabilidad? Por favor, no abras un issue público: infórmala a través de
[Security Advisories](https://github.com/yggdrasil-sh/saga/security/advisories/new).

## Desarrollo y pruebas

Las pruebas se ejecutan contra un stack real con PostgreSQL, Radicale (CalDAV), un OpenLDAP de prueba y un
proveedor OIDC de prueba:

```sh
SAGA_SSH=root@dockerhost tools/test-stack.sh
```

Capturas para esta página: `SAGA_SSH=… SAGA_SCRIPT=screens-remote.sh tools/test-stack.sh`.

## Licencia

[GNU AGPL-3.0](LICENSE): puedes usar, modificar y compartir Saga libremente; si ofreces una versión modificada
como servicio a otras personas, también tienes que publicar su código fuente. Los componentes de Editor.js
incluidos tienen sus propias licencias (`static/vendor/editorjs/licenses`). Iconos de servicios de
[selfh.st/icons](https://selfh.st/icons).
