"""
DigitalOcean Spaces Storage Service (S3 Compatible).
Permite almacenar archivos multimedia (fotos, PDFs de fichas técnicas) en DigitalOcean Spaces
cuando las credenciales están configuradas en el entorno, o mantener almacenamiento local en disco.
"""
import os
import logging
from typing import Optional, Dict, Any
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


def upload_to_spaces(
    file_bytes: bytes,
    filename: str,
    content_type: str = "application/octet-stream",
    folder: str = "media"
) -> Optional[Dict[str, Any]]:
    """
    Sube un archivo directamente al bucket de DigitalOcean Spaces con ACL public-read.
    Retorna la URL pública HTTPS del CDN de Spaces.
    """
    if not is_spaces_configured():
        return None

    access_key = os.environ.get("SPACES_ACCESS_KEY")
    secret_key = os.environ.get("SPACES_SECRET_KEY")
    bucket_name = os.environ.get("SPACES_BUCKET_NAME")
    region = os.environ.get("SPACES_REGION", "nyc3")
    endpoint_url = f"https://{region}.digitaloceanspaces.com"

    try:
        session = boto3.session.Session()
        client = session.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(s3={"addressing_style": "virtual"})
        )

        key = f"{folder}/{filename}".lstrip("/")
        client.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=file_bytes,
            ACL="public-read",
            ContentType=content_type,
            CacheControl="max-age=2592000, public"  # 30 días en caché de CDN
        )

        public_url = f"https://{bucket_name}.{region}.digitaloceanspaces.com/{key}"
        logger.info(f"✅ Archivo subido exitosamente a DigitalOcean Spaces: {public_url}")
        return {
            "success": True,
            "url": public_url,
            "key": key,
            "storage": "spaces"
        }
    except Exception as e:
        logger.error(f"❌ Error subiendo a DigitalOcean Spaces: {e}")
        return None
