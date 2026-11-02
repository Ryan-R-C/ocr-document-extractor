"""Falhas de processamento do núcleo, independentes de HTTP.

O mapeamento para código HTTP fica em `app/api/erros.py`; o avaliador usa as mesmas falhas.
Nenhuma mensagem aqui pode carregar dados pessoais do documento.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self

from app.api.contrato import AcaoSugerida, CodigoErro, MotivoErro, MotivoErroDetalhado


class FalhaProcessamento(Exception):
    codigo: CodigoErro = CodigoErro.DOCUMENTO_NAO_PROCESSAVEL
    acao_padrao: AcaoSugerida = AcaoSugerida.REENVIAR_IMAGEM

    def __init__(self, motivos: Sequence[MotivoErroDetalhado]) -> None:
        if not motivos:
            raise ValueError("falha sem motivo")
        self.motivos = list(motivos)
        super().__init__(", ".join(m.codigo.value for m in self.motivos))

    @classmethod
    def de(cls, codigo: MotivoErro, campo: str | None = None, detalhe: str | None = None) -> Self:
        return cls([MotivoErroDetalhado(codigo=codigo, campo=campo, detalhe=detalhe)])

    @property
    def acao_sugerida(self) -> AcaoSugerida:
        return self.acao_padrao


class FalhaRequisicao(FalhaProcessamento):
    """400: a requisição está errada."""

    codigo = CodigoErro.REQUISICAO_INVALIDA
    acao_padrao = AcaoSugerida.CORRIGIR_REQUISICAO


class FalhaArquivoGrande(FalhaProcessamento):
    """413: arquivo ou resolução acima do limite."""

    codigo = CodigoErro.ARQUIVO_GRANDE_DEMAIS
    acao_padrao = AcaoSugerida.REENVIAR_IMAGEM


class FalhaExtracao(FalhaProcessamento):
    """422 (configurável): imagem válida, mas sem como extrair os campos críticos."""

    codigo = CodigoErro.DOCUMENTO_NAO_PROCESSAVEL

    @property
    def acao_sugerida(self) -> AcaoSugerida:
        if all(m.codigo is MotivoErro.TIPO_DIVERGENTE for m in self.motivos):
            return AcaoSugerida.CORRIGIR_REQUISICAO
        return AcaoSugerida.REENVIAR_IMAGEM


class FalhaProviderIndisponivel(FalhaProcessamento):
    """502: provider falhou mesmo após nova tentativa e fallback."""

    codigo = CodigoErro.PROVIDER_INDISPONIVEL
    acao_padrao = AcaoSugerida.TENTAR_NOVAMENTE


class FalhaTempoEsgotado(FalhaProcessamento):
    """504: provider estourou o tempo mesmo após nova tentativa e fallback."""

    codigo = CodigoErro.TEMPO_ESGOTADO
    acao_padrao = AcaoSugerida.TENTAR_NOVAMENTE
