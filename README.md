# QS Ajuda de Custos — V0.9.2

Versão local de validação da operação quinzenal da QSPROMO, com foco em uso diário, perfis de acesso e histórico individual por colaborador.

## Principais melhorias desta versão

- **Administrativo / outros fora do escopo operacional**: os 31 registros desse grupo permanecem preservados no banco, mas não aparecem na rotina normal, não entram no processamento e não afetam totais. Administradores podem consultá-los separadamente.
- **Início redesenhado como central de trabalho**: mostra a quinzena atual, Total Conhecido, itens que precisam de atenção e atalhos para ocorrência, quinzena, ajustes e pessoas.
- **Registro de ocorrência simplificado**: primeiro o usuário escolhe o que aconteceu (férias, falta, atestado, licença, afastamento, passagem adicional, lojas próximas, viagem/apoio ou outro). Depois o QS mostra os campos necessários e sugere o impacto.
- **Férias, faltas, licenças e afastamentos no calendário do colaborador**:
  - férias e faltas podem retirar dias elegíveis;
  - atestados, licenças e afastamentos começam como revisão por segurança, até a política do setor ser confirmada;
  - todos ficam registrados no histórico individual.
- **Calendário no perfil do colaborador**, com feriados e ocorrências por dia.
- **Política individual por colaborador**: o administrador pode sobrescrever dias da semana e/ou quantidade de VT/dia somente naquele perfil, com motivo e vigência. A regra geral não é alterada.
- **Identidade visual QS**: interface clara com azul, branco e amarelo como cores principais.
- **Login e permissões**:
  - `ADMIN`: configura regras, tarifas, feriados, usuários e ajustes individuais;
  - `BENEFICIOS`: registra ocorrências, processa quinzenas e opera reembolsos/ajustes;
  - `CONSULTA`: somente visualização.
- **Auditoria**: registra as principais ações, como login, criação de usuário, ocorrência, tarifa, processamento, ajuste financeiro e política individual.
- Mantidos da V0.9.1: tarifas cadastráveis, feriados 2026, passagens adicionais, lojas próximas, reembolsos/descontos e Total Conhecido.

## Primeira abertura

A V0.9.2 utiliza o mesmo banco persistente das versões anteriores. Na primeira abertura desta versão, se ainda não existir nenhum usuário, o sistema solicitará a criação do **primeiro administrador**.

A senha deve possuir pelo menos 8 caracteres.

## Banco persistente

No Windows, normalmente:

`%LOCALAPPDATA%\QS_Ajuda_Custos\qs_ajuda_custos.db`

A atualização da versão não apaga homologações, tarifas, ocorrências, cálculos ou ajustes já existentes. A V0.9.2 também migra automaticamente o banco anterior para incluir os novos campos/tabelas.

## Rodar no Windows

1. Encerre a versão anterior com `CTRL+C`.
2. Extraia o ZIP da V0.9.2 em uma pasta nova.
3. Execute `run_windows.bat`.
4. Abra `http://127.0.0.1:8000`.
5. Na primeira abertura, crie o administrador.

## Teste recomendado

1. Abra **Início** e confira a central da quinzena atual.
2. Abra **Pessoas → Colaboradores** e confirme que Administrativo/outros não aparece na operação normal.
3. Abra um colaborador e confira o **calendário individual**.
4. Como administrador, crie um **ajuste individual de política** e confirme que ele fica apenas naquele perfil.
5. Registre uma ocorrência de férias/licença e veja o evento no calendário.
6. Crie um usuário de **Benefícios** e um de **Consulta** em `Administração → Usuários e permissões`.
7. Confira `Administração → Auditoria` depois de executar algumas ações.

## Regras ainda em validação

- Distância automática entre PDVs via Puzzle. A regra de lojas próximas continua disponível manualmente e preparada para futura automação pela regra de até 750 m.
- Política financeira exata para cada subtipo de licença/atestado/afastamento. Enquanto não homologada, o QS pode encaminhar o cálculo para revisão em vez de assumir uma regra.
- Moto, carro, KM e VR/refeição continuam fora do motor financeiro de VT desta etapa quando não existe regra confirmada.
