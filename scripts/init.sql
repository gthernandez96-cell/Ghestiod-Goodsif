-- Inicialización de PostgreSQL para POS Empresarial
-- Se ejecuta automáticamente al crear el contenedor

-- Extensiones necesarias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- Para búsqueda de texto
CREATE EXTENSION IF NOT EXISTS "unaccent"; -- Para búsqueda sin acentos

-- Función para búsqueda sin acentos
CREATE OR REPLACE FUNCTION unaccent_text(text) RETURNS text AS $$
BEGIN
    RETURN unaccent($1);
EXCEPTION WHEN OTHERS THEN
    RETURN $1;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Configuraciones de rendimiento
ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';
ALTER SYSTEM SET track_activity_query_size = 2048;
