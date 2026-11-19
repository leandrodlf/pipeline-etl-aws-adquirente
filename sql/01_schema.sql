-- Esquema del origen simulado: adquirente de pagos.
-- Subconjunto núcleo de la base de práctica (12 de 44 tablas), con las mismas relaciones.
-- Todos los datos son simulados.

DROP TABLE IF EXISTS contracargos, transacciones, lotes_cierre, liquidaciones, tarjetas,
    terminales, sucursales, comercios, codigos_respuesta, modelos_terminal,
    bancos_emisores, rubros CASCADE;

CREATE TABLE rubros (
    id          integer PRIMARY KEY,
    nombre      varchar(60) NOT NULL,
    codigo_mcc  char(4)     NOT NULL
);

CREATE TABLE bancos_emisores (
    id      integer PRIMARY KEY,
    nombre  varchar(80) NOT NULL,
    pais    varchar(2)  NOT NULL
);

CREATE TABLE modelos_terminal (
    id      integer PRIMARY KEY,
    marca   varchar(40) NOT NULL,
    modelo  varchar(40) NOT NULL,
    tipo    varchar(20) NOT NULL
);

CREATE TABLE codigos_respuesta (
    id           integer PRIMARY KEY,
    codigo       char(2)     NOT NULL,
    descripcion  varchar(80) NOT NULL,
    tipo         varchar(20) NOT NULL
);

CREATE TABLE comercios (
    id                integer PRIMARY KEY,
    rut               varchar(12)  NOT NULL UNIQUE,
    razon_social      varchar(150) NOT NULL,
    nombre_fantasia   varchar(150) NOT NULL,
    rubro_id          integer REFERENCES rubros(id),
    region            varchar(40),
    fecha_afiliacion  date,
    estado            varchar(15)
);

CREATE TABLE sucursales (
    id          integer PRIMARY KEY,
    comercio_id integer NOT NULL REFERENCES comercios(id),
    nombre      varchar(150),
    comuna      varchar(60),
    direccion   varchar(150)
);

CREATE TABLE terminales (
    id                 integer PRIMARY KEY,
    numero_serie       varchar(20) NOT NULL UNIQUE,
    modelo_id          integer REFERENCES modelos_terminal(id),
    sucursal_id        integer REFERENCES sucursales(id),   -- NULL = terminal en bodega
    estado             varchar(15),
    fecha_instalacion  date
);

CREATE TABLE tarjetas (
    id               integer PRIMARY KEY,
    bin              char(6) NOT NULL,
    marca            varchar(15),
    tipo             varchar(15),
    banco_emisor_id  integer REFERENCES bancos_emisores(id)
);

CREATE TABLE liquidaciones (
    id           integer PRIMARY KEY,
    comercio_id  integer NOT NULL REFERENCES comercios(id),
    fecha_pago   date,
    monto_bruto  numeric(14,2),
    comision     numeric(14,2),
    monto_neto   numeric(14,2),
    estado       varchar(15)
);

CREATE TABLE lotes_cierre (
    id            integer PRIMARY KEY,
    terminal_id   integer NOT NULL REFERENCES terminales(id),
    fecha_cierre  timestamp,
    cantidad_tx   integer,
    monto_total   numeric(14,2),
    estado        varchar(15)
);

CREATE TABLE transacciones (
    id                   integer PRIMARY KEY,
    terminal_id          integer NOT NULL REFERENCES terminales(id),
    tarjeta_id           integer NOT NULL REFERENCES tarjetas(id),
    liquidacion_id       integer REFERENCES liquidaciones(id),  -- NULL = aún sin liquidar
    fecha_hora           timestamp NOT NULL,
    monto                numeric(14,2) NOT NULL,
    cuotas               integer,
    estado               varchar(15),
    codigo_autorizacion  varchar(10),
    codigo_respuesta_id  integer REFERENCES codigos_respuesta(id),
    lote_id              integer REFERENCES lotes_cierre(id)
);

CREATE TABLE contracargos (
    id              integer PRIMARY KEY,
    transaccion_id  integer NOT NULL REFERENCES transacciones(id),
    motivo          varchar(60),
    monto           numeric(14,2),
    fecha           date,
    estado          varchar(15)
);

CREATE INDEX idx_tx_fecha    ON transacciones (fecha_hora);
CREATE INDEX idx_tx_terminal ON transacciones (terminal_id);
CREATE INDEX idx_tx_liq      ON transacciones (liquidacion_id);
