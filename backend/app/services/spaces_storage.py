"""
DigitalOcean Spaces Storage Service (S3 Compatible).
Permite almacenar archivos multimedia (fotos, PDFs de fichas técnicas) y respaldos automáticos
en DigitalOcean Spaces cuando las credenciales están configuradas en el entorno.
"""
import os
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.client import Config
    BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None
    Config = None
    BOTO3_AVAILABLE = False


def is_spaces_configured() -> bool:
    """Verifica si boto3 está disponible y las credenciales de DigitalOcean Spaces están configuradas."""
    return bool(
        BOTO3_AVAILABLE and
        os.environ.get("SPACES_ACCESS_KEY") and
        os.environ.get("SPACES_SECRET_KEY") and
        os.environ.get("SPACES_BUCKET_NAME")
    )


def get_spaces_client():
    """Obtiene un cliente boto3 S3 configurado para DigitalOcean Spaces."""
    if not is_spaces_configured():
        return None
    access_key = os.environ.get("SPACES_ACCESS_KEY")
    secret_key = os.environ.get("SPACES_SECRET_KEY")
    region = os.environ.get("SPACES_REGION", "atl1")
    endpoint_url = f"https://{region}.digitaloceanspaces.com"

    try:
        session = boto3.session.Session()
        return session.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(s3={"addressing_style": "virtual"})
        )
    except Exception as e:
        logger.error(f"❌ Error inicializando cliente DigitalOcean Spaces: {e}")
        return None


def upload_to_spaces(
    file_bytes: bytes,
    filename: str,
    content_type: str = "application/octet-stream",
    folder: str = "media",
    is_private: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Sube un archivo directamente al bucket de DigitalOcean Spaces.
    Retorna la URL pública HTTPS o detalles del archivo subido.
    """
    client = get_spaces_client()
    if not client:
        return None

    bucket_name = os.environ.get("SPACES_BUCKET_NAME")
    region = os.environ.get("SPACES_REGION", "atl1")

    try:
        key = f"{folder}/{filename}".lstrip("/")
        acl_type = "private" if is_private else "public-read"

        client.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=file_bytes,
            ACL=acl_type,
            ContentType=content_type,
            CacheControl="max-age=2592000, public" if not is_private else "no-cache"
        )

        public_url = f"https://{bucket_name}.{region}.digitaloceanspaces.com/{key}"
        logger.info(f"✅ Archivo subido exitosamente a DigitalOcean Spaces: {public_url}")
        return {
            "success": True,
            "url": public_url,
            "key": key,
            "size": len(file_bytes),
            "storage": "spaces"
        }
    except Exception as e:
        logger.error(f"❌ Error subiendo a DigitalOcean Spaces: {e}")
        return None


def list_spaces_files(prefix: str = "backups/") -> List[Dict[str, Any]]:
    """Lista archivos alojados en DigitalOcean Spaces bajo un prefijo/carpeta."""
    client = get_spaces_client()
    if not client:
        return []

    bucket_name = os.environ.get("SPACES_BUCKET_NAME")
    region = os.environ.get("SPACES_REGION", "atl1")

    try:
        resp = client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        contents = resp.get("Contents", [])
        files = []
        for item in sorted(contents, key=lambda x: x["LastModified"], reverse=True):
            key = item["Key"]
            filename = os.path.basename(key)
            if not filename:
                continue
            size_bytes = item["Size"]
            pretty_size = (
                f"{size_bytes / (1024 * 1024):.2f} MB"
                if size_bytes >= 1024 * 1024
                else f"{size_bytes / 1024:.1f} KB"
            )
            url = f"https://{bucket_name}.{region}.digitaloceanspaces.com/{key}"
            files.append({
                "key": key,
                "filename": filename,
                "size_bytes": size_bytes,
                "pretty_size": pretty_size,
                "last_modified": item["LastModified"].strftime("%d/%m/%Y %H:%M:%S UTC"),
                "raw_date": item["LastModified"].isoformat(),
                "url": url
            })
        return files
    except Exception as e:
        logger.error(f"❌ Error listando archivos de Spaces ({prefix}): {e}")
        return []


def delete_from_spaces(key: str) -> bool:
    """Elimina un archivo de DigitalOcean Spaces."""
    client = get_spaces_client()
    if not client:
        return False
    bucket_name = os.environ.get("SPACES_BUCKET_NAME")
    try:
        client.delete_object(Bucket=bucket_name, Key=key)
        logger.info(f"🗑️ Archivo eliminado de DigitalOcean Spaces: {key}")
        return True
    except Exception as e:
        logger.error(f"❌ Error eliminando {key} de Spaces: {e}")
        return False
