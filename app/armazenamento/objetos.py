"""Armazenamento de objetos compatível com S3. Todo objeto é gravado com criptografia em repouso."""

from __future__ import annotations

import contextlib
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.armazenamento.retencao import regras_lifecycle
from app.config import Settings


class Objetos:
    def __init__(self, settings: Settings, cliente: Any | None = None) -> None:
        self.settings = settings
        self.bucket = settings.s3_bucket
        self.cliente = cliente or boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_regiao,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"}, s3={"addressing_style": "path"}
            ),
        )

    def _sse(self) -> dict[str, str]:
        extra: dict[str, str] = {"ServerSideEncryption": self.settings.s3_sse}
        if self.settings.s3_sse == "aws:kms" and self.settings.s3_sse_kms_key_id:
            extra["SSEKMSKeyId"] = self.settings.s3_sse_kms_key_id
        return extra

    def preparar_bucket(self) -> None:
        """Cria o bucket se preciso, liga criptografia padrão, bloqueia acesso público e
        aplica o lifecycle de segurança. Idempotente."""
        try:
            self.cliente.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.cliente.create_bucket(Bucket=self.bucket)
        regra_sse: dict[str, Any] = {"SSEAlgorithm": self.settings.s3_sse}
        if self.settings.s3_sse == "aws:kms" and self.settings.s3_sse_kms_key_id:
            regra_sse["KMSMasterKeyID"] = self.settings.s3_sse_kms_key_id
        self.cliente.put_bucket_encryption(
            Bucket=self.bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": regra_sse}]
            },
        )
        # Nem todo S3 compatível implementa o bloqueio de acesso público; o bucket já nasce privado.
        with contextlib.suppress(ClientError):
            self.cliente.put_public_access_block(
                Bucket=self.bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
        self.cliente.put_bucket_lifecycle_configuration(
            Bucket=self.bucket, LifecycleConfiguration={"Rules": regras_lifecycle(self.settings)}
        )

    def gravar(self, chave: str, conteudo: bytes, content_type: str) -> None:
        self.cliente.put_object(
            Bucket=self.bucket, Key=chave, Body=conteudo, ContentType=content_type, **self._sse()
        )

    def ler(self, chave: str) -> bytes | None:
        try:
            resposta = self.cliente.get_object(Bucket=self.bucket, Key=chave)
        except ClientError as erro:
            if erro.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return None
            raise
        conteudo: bytes = resposta["Body"].read()
        return conteudo

    def listar(self, prefixo: str) -> list[str]:
        chaves: list[str] = []
        paginador = self.cliente.get_paginator("list_objects_v2")
        for pagina in paginador.paginate(Bucket=self.bucket, Prefix=prefixo):
            chaves.extend(item["Key"] for item in pagina.get("Contents", []))
        return chaves

    def apagar_prefixo(self, prefixo: str) -> int:
        """Apaga todos os objetos sob `prefixo/`. Idempotente; devolve quantos apagou."""
        chaves = self.listar(prefixo.rstrip("/") + "/")
        for inicio in range(0, len(chaves), 1000):
            lote = chaves[inicio : inicio + 1000]
            resposta = self.cliente.delete_objects(
                Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in lote], "Quiet": True}
            )
            if resposta.get("Errors"):
                raise RuntimeError(f"falha ao apagar {len(resposta['Errors'])} objetos")
        return len(chaves)

    def copiar_prefixo(self, origem: str, destino: str) -> None:
        origem = origem.rstrip("/") + "/"
        for chave in self.listar(origem):
            self.cliente.copy_object(
                Bucket=self.bucket,
                Key=destino.rstrip("/") + "/" + chave[len(origem) :],
                CopySource={"Bucket": self.bucket, "Key": chave},
                **self._sse(),
            )

    def ok(self) -> bool:
        try:
            self.cliente.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False
