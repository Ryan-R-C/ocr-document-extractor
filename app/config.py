"""Configuração única do serviço, lida de variáveis de ambiente e `.env`.

Limiares, preços, câmbio, nomes de modelo e limites de concorrência vivem aqui,
nunca como constantes espalhadas pelo código.
"""

from __future__ import annotations

import re
from datetime import timedelta
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, Field, NonNegativeFloat, PositiveInt, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DURACAO = re.compile(r"^(?P<n>\d+)(?P<u>[mhd])$")
_UNIDADES = {"m": "minutes", "h": "hours", "d": "days"}


def interpretar_duracao(valor: str | None) -> timedelta | None:
    """Converte `8h`, `16h`, `30d`, `90m` em timedelta; `null`, vazio ou None = sem expiração."""
    if valor is None:
        return None
    texto = valor.strip().lower()
    if texto in {"", "null", "none"}:
        return None
    casamento = _DURACAO.match(texto)
    if not casamento:
        raise ValueError(f"duração inválida: {valor!r} (use 8h, 16h, 30d, 90m ou null)")
    n = int(casamento["n"])
    if n <= 0:
        raise ValueError("duração precisa ser positiva")
    return timedelta(**{_UNIDADES[casamento["u"]]: n})


class PrecoModelo(BaseModel):
    """Preço padrão em US$ por milhão de tokens."""

    entrada: NonNegativeFloat
    saida: NonNegativeFloat
    # Desconto das rodadas mais baratas (Batch/Flex); 0,5 = metade do preço padrão.
    fator_batch: NonNegativeFloat = 0.5
    fator_flex: NonNegativeFloat = 0.5


class RegraImagemModelo(BaseModel):
    """Como o provider conta tokens de imagem: patches de `patch_px` × multiplicador.

    O plano documenta 32 px e multiplicador 1,2 só para o gpt-6-astra; os demais são
    provisórios (conservadores) até a conferência na documentação/calculadora oficial.
    """

    patch_px: PositiveInt = 32
    multiplicador: NonNegativeFloat = 1.2
    patches_max: PositiveInt = 30_000


class LimiteProvider(BaseModel):
    concorrencia: PositiveInt = 8
    tokens_por_minuto: PositiveInt = 2_000_000
    # Estimativa reservada antes da chamada; o valor real é acertado depois.
    tokens_estimados_por_chamada: PositiveInt = 4_000


PRECOS_PADRAO: dict[str, PrecoModelo] = {
    # Fonte: plano, seção "Custos estimados" (preços padrão por milhão de tokens).
    "gpt-6-luna": PrecoModelo(entrada=0.10, saida=0.50),
    "gpt-6.1-terra": PrecoModelo(entrada=1.0, saida=5.0),
    "gpt-6.1-sol": PrecoModelo(entrada=2.0, saida=10.0),
    "gpt-6-astra": PrecoModelo(entrada=10.0, saida=50.0),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Providers -------------------------------------------------------------------------
    leitor_visual_provider: str = "openai"
    leitor_visual_modelo: str = "gpt-6.1-terra"
    leitor_visual_modelo_escalada: str = "gpt-6.1-sol"
    leitor_visual_detalhe: Literal["low", "high", "original", "auto"] = "original"
    # unico: classificação + campos de todos os subtipos numa chamada (anyOf);
    # duas_etapas: classificação barata e depois extração com o schema do subtipo.
    leitor_visual_desenho: Literal["unico", "duas_etapas"] = "unico"
    leitor_visual_modelo_classificacao: str = "gpt-6-luna"
    # Menor esforço disponível por padrão; None omite o parâmetro.
    leitor_visual_esforco_raciocinio: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None
    ) = "none"
    leitor_visual_max_tokens_saida: PositiveInt = 4_000
    modelos_imagem: dict[str, RegraImagemModelo] = Field(default_factory=dict)
    leitor_texto_provider: str = "paddleocr"
    localizador_provider: str = "ocr"
    provider_fallback: str | None = None
    provider_timeout_s: Annotated[float, Field(gt=0)] = 30.0
    # Novas tentativas em 429/5xx/erro de conexão, com espera exponencial e jitter.
    provider_tentativas_max: PositiveInt = 4
    provider_espera_inicial_s: Annotated[float, Field(ge=0)] = 0.5
    provider_espera_max_s: Annotated[float, Field(ge=0)] = 8.0
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    # Diretório com as respostas gravadas usadas pelos providers `fake`.
    fake_gravacoes_dir: str = "gravacoes"

    # --- Retenção --------------------------------------------------------------------------
    artefatos_ttl: Literal["8h", "16h"] | None = "8h"
    casos_revisao_ttl: str | None = None
    limpeza_intervalo_s: PositiveInt = 300

    # --- API -------------------------------------------------------------------------------
    http_status_falha_extracao: Literal[400, 422] = 422
    arquivo_max_bytes: PositiveInt = 10 * 1024 * 1024
    imagem_pixels_max: PositiveInt = 50_000_000
    imagem_lado_max_px: PositiveInt = 2048
    pdf_dpi: PositiveInt = 200
    # Documento recortado cabe em ~1600 × 1000 px antes de ir ao provider.
    alvo_lado_maior_px: PositiveInt = 1600
    alvo_lado_menor_px: PositiveInt = 1000
    # Localização do documento: área relativa aceita para o quadrilátero e margem de borda.
    documento_area_min: Annotated[float, Field(ge=0, le=1)] = 0.2
    documento_area_max: Annotated[float, Field(ge=0, le=1)] = 0.97
    documento_margem_borda: Annotated[float, Field(ge=0, le=0.2)] = 0.01
    # Limiares de qualidade (provisórios; calibrar na partição de ajuste). Reprovação = 422.
    qualidade_conteudo_min: Annotated[float, Field(ge=0)] = 3.0
    qualidade_nitidez_min: Annotated[float, Field(ge=0)] = 15.0
    qualidade_reflexo_max: Annotated[float, Field(ge=0, le=1)] = 0.08
    qualidade_corte_max: Annotated[float, Field(ge=0, le=1)] = 0.2
    # Heurísticas de captura (só sinalizam) e como combiná-las com a classificação do VLM.
    captura_copia_saturacao_max: Annotated[float, Field(ge=0)] = 10.0
    captura_print_repetidos_min: Annotated[float, Field(ge=0, le=1)] = 0.6
    captura_print_ruido_max: Annotated[float, Field(ge=0)] = 0.8
    sinalizacao_captura_fonte: Literal["vlm", "heuristica", "qualquer", "ambas"] = "qualquer"
    # Ver docs/decisoes.md: por padrão, NAO_VERIFICAVEL em campo crítico impede APROVADO.
    aprovar_critico_nao_verificavel: bool = False

    # --- Banco e objetos -------------------------------------------------------------------
    database_url: str = "postgresql+psycopg://extracao:extracao@localhost:5432/extracao"
    database_pool_size: PositiveInt = 5
    database_max_overflow: int = 10
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_regiao: str = "us-east-1"
    s3_bucket: str = "extracao-auditoria"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    # AES256 (SSE-S3) ou aws:kms; nenhum objeto é gravado sem criptografia em repouso.
    s3_sse: Literal["AES256", "aws:kms"] = "AES256"
    s3_sse_kms_key_id: str | None = None

    # --- Custos e limites ------------------------------------------------------------------
    precos_modelos: dict[str, PrecoModelo] = Field(default_factory=lambda: dict(PRECOS_PADRAO))
    cambio_usd_brl: Annotated[float, Field(gt=0)] = 5.40
    provider_limites: dict[str, LimiteProvider] = Field(default_factory=dict)

    # --- Observabilidade -------------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    ambiente: str = "local"

    @field_validator("artefatos_ttl", mode="before")
    @classmethod
    def _ttl_artefatos(cls, valor: object) -> object:
        if isinstance(valor, str) and valor.strip().lower() in {"", "null", "none"}:
            return None
        return valor

    @field_validator("casos_revisao_ttl", mode="before")
    @classmethod
    def _ttl_revisao(cls, valor: object) -> object:
        if valor is None:
            return None
        texto = str(valor)
        interpretar_duracao(texto)  # valida
        return None if texto.strip().lower() in {"", "null", "none"} else texto.strip().lower()

    @field_validator("http_status_falha_extracao", mode="before")
    @classmethod
    def _status_inteiro(cls, valor: object) -> object:
        # Variável de ambiente chega como texto; Literal[400, 422] não converte sozinho.
        if isinstance(valor, str) and valor.strip().isdigit():
            return int(valor)
        return valor

    @field_validator(
        "provider_fallback",
        "leitor_visual_esforco_raciocinio",
        "openai_api_key",
        "openai_base_url",
        mode="before",
    )
    @classmethod
    def _fallback_vazio(cls, valor: object) -> object:
        if isinstance(valor, str) and not valor.strip():
            return None
        return valor

    @property
    def ttl_artefatos(self) -> timedelta | None:
        return interpretar_duracao(self.artefatos_ttl)

    @property
    def ttl_casos_revisao(self) -> timedelta | None:
        return interpretar_duracao(self.casos_revisao_ttl)

    def limite_provider(self, provider: str) -> LimiteProvider:
        return self.provider_limites.get(provider, LimiteProvider())

    def regra_imagem(self, modelo: str) -> RegraImagemModelo:
        if modelo in self.modelos_imagem:
            return self.modelos_imagem[modelo]
        return RegraImagemModelo()


@lru_cache(maxsize=1)
def obter_settings() -> Settings:
    return Settings()
