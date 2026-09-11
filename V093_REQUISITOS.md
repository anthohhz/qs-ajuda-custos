# QS Ajuda de Custos — Requisitos confirmados da V0.9.3

Este documento registra as regras de negócio confirmadas para a evolução da V0.9.2. Ele serve como referência para desenvolvimento e homologação da V0.9.3.

## 1. Ciclo de pagamento

- A QS trabalha com duas quinzenas por mês.
- **1ª quinzena:** dia 01 até o dia 17, inclusive.
- **2ª quinzena:** dia 18 até o último dia do mês.
- O **Reporte Financeiro é gerado por quinzena**.
- Uma quinzena fechada não pode ser recalculada silenciosamente.
- Correções posteriores ao fechamento devem entrar como ajuste, desconto, crédito ou reembolso em período posterior, mantendo a referência original.

## 2. Preservação histórica

- A troca de mês não pode apagar, substituir ou recalcular automaticamente competências anteriores.
- Rotas, tarifas, benefícios, ocorrências e demais dados variáveis devem preservar vigência/histórico.
- Uma quinzena fechada deve manter um snapshot dos valores aprovados.
- O Reporte gerado deve ficar associado ao fechamento correspondente.
- Registros operacionais relevantes devem ser cancelados/inativados com auditoria em vez de apagados fisicamente quando isso comprometer o histórico.

## 3. Alimentação

- **6h:** Lanche de **R$ 10,00 por dia**.
- **7h ou mais:** VR.
- VR padrão: **R$ 22,00 por dia**.
- VR Centro-Oeste: **R$ 25,00 por dia**.
- VR Sergipe: **R$ 27,00 por dia**.
- O lanche é **R$ 10,00 em qualquer região**.
- Férias retiram VT e o benefício de alimentação do período correspondente.

## 4. Transporte

- O transporte pode ser calculado por VT ou por KM conforme o colaborador/regra aplicável.
- KM é permitido para colaboradores autorizados pela empresa, incluindo supervisores e casos de promotores com rotas longas autorizadas.
- Colaboradores com KM devem ter a autorização registrada no sistema.
- Trechos entre PDVs com distância de até **750 m** não devem gerar passagem adicional de VT.
- A regra de proximidade deve migrar de ocorrência manual para regra automática baseada no roteiro/PDVs.

## 5. KM e combustível

- Quem recebe por KM precisa usar evidência de preço de combustível em postos autorizados pela QS.
- A evidência deve aceitar foto/documento e passar por validação.
- O preço aprovado deve ter vigência e ser utilizado pelo motor de cálculo enquanto válido.
- Fotos/evidências não devem ser armazenadas no GitHub.

## 6. Calendário do colaborador

O calendário será a principal visão cronológica do colaborador e deve consolidar, por dia:

- rota e PDVs;
- feriados aplicáveis;
- faltas;
- atestados e documentos;
- férias;
- afastamentos/licenças conforme permissão;
- alterações operacionais;
- VT ou KM;
- VR ou lanche;
- futuramente VA;
- descontos;
- reembolsos;
- histórico/auditoria.

O período de uma ocorrência deve permanecer visível integralmente no calendário, mesmo quando apenas parte dos dias produzir impacto financeiro.

## 7. Ocorrências

- A tela não deve expor termos internos do motor como `REMOVE_DAY`, `ADD_VT`, `REMOVE_VT`, `BLOCK` ou `INFO`.
- Cada tipo de ocorrência deve mostrar somente os campos necessários para aquele evento.
- Atestado deve permitir upload do documento no próprio registro.
- Os eventos exibidos devem respeitar o setor/permissão do usuário.
- `Outro` deve ser evitado para impedir registros sem estrutura.
- Passagem adicional, lojas próximas e apoio/alteração de roteiro devem migrar para o contexto operacional/rota, e não permanecer como ocorrências genéricas.

## 8. Pessoas, empresas e setores

- Colaborador deve ser vinculado a uma Empresa.
- Usuário do sistema deve ser vinculado a um Setor interno.
- Empresa do colaborador e Setor do usuário são conceitos diferentes.
- Setores internos inicialmente considerados: Sistemas, Ajuda de Custos, RH e Comercial.
- CPF deve ser armazenado como texto de 11 dígitos, sem pontuação.
- O cadastro deve suportar grupo operacional, situação do colaborador, fornecedor/exclusividade quando aplicável e autorização de KM.

## 9. PDVs e roteiros

- PDVs devem ser entidades próprias do sistema.
- Roteiros devem ser criados e mantidos nativamente no QS, vinculando colaborador, vigência, dia da semana, PDVs e ordem de visita.
- O histórico de roteiro deve ser preservado por vigência.
- A importação de roteiro via planilha do Puzzle é transitória e deverá deixar de ser necessária.
- PDVs devem evoluir para cidade/UF padronizadas e coordenadas geográficas.
- Integração futura com Google Maps/Routes deverá calcular distância entre PDVs e permitir visualização em mapa.

## 10. Localidades, tarifas e feriados

- Cidade não deve ser digitada livremente em cadastro de feriado ou tarifa.
- Usuário seleciona UF e depois uma cidade cadastrada/padronizada.
- O sistema deve detectar e impedir feriados duplicados para a mesma data/escopo/localidade.
- Tarifas devem preservar histórico de vigência; alteração de tarifa não pode sobrescrever valores usados em períodos anteriores.

## 11. Fechamento e Reporte Financeiro

Fluxo alvo:

`Quinzena aberta → cálculo atualizado → revisão → pendências resolvidas → aprovação da liderança → fechamento → Reporte XLSX`

- O termo `Processar/Reprocessar` deve deixar de ser a ação principal da operação.
- Recalcular pode permanecer como função técnica de Sistemas/Admin enquanto o período estiver aberto.
- O formato inicial do Reporte deve manter compatibilidade com o arquivo que o Financeiro já recebe.
- Campos-base confirmados do Reporte atual: Colaborador, CPF, Passagem/KM, Almoço, Desconto, Motivo, Reembolso, Motivo e Total com descontos.

## 12. Legado da V0.9.2

- `Políticas de Cálculo` e `Regras Homologadas` deixam de ser funcionalidades normais visíveis ao usuário.
- As tabelas/estruturas antigas podem permanecer temporariamente enquanto o novo motor é construído, para não quebrar a aplicação.
- O motor V0.9.2 deve ser aposentado somente após comparação e homologação dos resultados do novo motor.

## Estratégia de implementação

1. Corrigir o ciclo financeiro para 01–17 / 18–fim sem alterar períodos fechados.
2. Criar a fundação corporativa e de histórico.
3. Refatorar Ocorrências.
4. Padronizar localidades.
5. Evoluir PDVs e criar roteiros nativos.
6. Criar Calendário V2.
7. Criar motor diário VT/KM + VR/Lanche.
8. Criar fechamento com snapshot.
9. Gerar Reporte Financeiro por quinzena.
10. Integrar Google Maps/Routes e automatizar a regra de 750 m.
