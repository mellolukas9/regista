# ADR 0019: S3 local de desenvolvimento com SeaweedFS

- **Status:** aceita
- **Data:** 2026-10

## Contexto

O M3 guarda capturas de tela em um armazenamento de objetos, enviadas pelo agente por URL pré-assinada direto para o S3 (o servidor não recebe o arquivo). Em produção será o S3 da AWS. Em desenvolvimento e nos testes precisamos de um S3 local. A edição community do MinIO, que seria a escolha natural, teve o repositório arquivado em abril de 2026 e não publica mais imagens nem correções.

## Decisão

Usar o **SeaweedFS** (`chrislusf/seaweedfs`, tag fixa) no `infra/compose/docker-compose.dev.yml` (porta 8333, volume próprio) e, nos testes, por Testcontainers com o mesmo arquivo de identidades (`infra/compose/seaweedfs/s3.json`). As credenciais desse arquivo só existem em desenvolvimento.

Em produção (M8): S3 da AWS acessado por **role IAM**, sem chave estática. Com `REGISTA_ENVIRONMENT=prod`, a API e o worker **se recusam a subir** se houver chave de acesso configurada ou se o endpoint for local ou não for https.

O critério de aceite é o teste `apps/api/tests/test_s3_smoke.py`, que passou contra o SeaweedFS 4.48: PUT pré-assinado com `Content-Type` e `Content-Length` assinados recusa corpo de outro tamanho ou tipo; URL de PUT vencida é recusada; GET pré-assinado respeita `response-content-type` e `response-content-disposition` forçados; URL de GET vencida é recusada; o bucket é privado.

## Consequências

Um contêiner a mais no ambiente de dev (um só processo). O código fala S3 padrão (`boto3`, assinatura v4, endereçamento por caminho), então trocar o provedor é configuração. O SeaweedFS não é a AWS: o que o teste de fumaça não cobre não deve ser assumido igual (por exemplo, políticas de bucket).

## Alternativas descartadas

| Opção | Motivo |
|---|---|
| MinIO (imagem antiga fixada) | Sem correções de segurança; repositório arquivado |
| S3Proxy | Depende do jclouds, que foi para o Apache Attic em 2025 (reserva caso o SeaweedFS falhe no teste de fumaça) |
| RustFS | Alfa (1.0.0-alpha) e vulnerabilidade recente |
| Garage | AGPL; inicialização em vários passos (layout, chaves); não é substituto direto |
| LocalStack / moto | Emulam em vez de armazenar; a imagem unificada do LocalStack pede conta |
