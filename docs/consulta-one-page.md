# Consulta de saúde dos clientes — uma página

Script que lê **Assurance › Health › Client** no Cisco Catalyst Center e grava CSV e dashboard em `relatorios/`. A senha é pedida na hora e não entra nos arquivos.

**Abrir:** `python consultar.py` (terminal) ou `python pagina.py` (página em `http://127.0.0.1:8780/`). Os dois usam o mesmo fluxo.

## O que o usuário informa

Host (padrão `https://dnac.boticario.net`), usuário, senha, tipo (todos, cabeado ou sem fio), SSID (obrigatório fora do tipo cabeado; vários separados por vírgula; `*` é curinga), localidade (trecho do nome; vazio = todas) e período (atalhos de 1 a 24 horas no terminal; data e hora na página).

Modo **todos** lista os clientes do período. Modo **reincidentes** lista quem repetiu conectividade baixa. Mínimo padrão: 3 ocorrências. Faixa padrão: score de 1 a 7 (0 é inativo e fica de fora). Na página a faixa pode ser qualquer par de 1 a 10.

TLS do certificado interno não é verificado. Cada chamada HTTP espera até 120 segundos.

## Etapas

1. **Autenticar.** `POST /dna/system/api/v1/auth/token`. O token vai em `X-Auth-Token`, renova em 50 minutos e, em HTTP 401, autentica de novo e repete a chamada uma vez.
2. **Achar a localidade.** `GET /dna/intent/api/v1/site`. Guarda as hierarquias que contêm o texto e os IDs desses sites e dos que estão abaixo. Se a lista falhar, usa o texto direto na API.
3. **Buscar clientes.** `GET /dna/data/api/v1/clients`, paginado de 500 em 500. Tenta, nesta ordem, até achar clientes no escopo: com SSID e tipo; com tipo; sem os dois; por fim, IDs de site em blocos de 25. MAC repetido é removido. Localidade, tipo e SSID são conferidos de novo localmente.
4. **Tratar falha da API.** HTTP 429 espera 60 segundos e tenta de novo. HTTP 400 na visão sem fio repete sem esse parâmetro. HTTP 404 na lista individual, no modo todos, cai em `site-health` (uma linha por localidade, sem MAC nem SSID). Com filtro de SSID ou no modo reincidentes, o 404 encerra com erro.
5. **Normalizar.** Cada cliente vira uma linha plana: identidade, scores, endereço, localidade, rádio, dispositivo conectado, tráfego e tempos. Colunas vazias saem do relatório.
6. **Reincidentes.** O período é fatiado (12 min até 1 h; 30 min até 3 h; 1 h até 6 h; 2 h até 12 h; 4 h até 24 h; no máximo 31 fatias). Em cada fatia, cada MAC com score na faixa conta uma vez. Entram no relatório os MACs que atingem o mínimo de fatias. A linha mostra ocorrências, pior score, pior RSSI/SNR, localidade mais frequente e os intervalos.
7. **Gravar e abrir.** `clientes-saude-AAAAMMDD-HHMMSS.csv/html` ou `clientes-reincidentes-…`. CSV em UTF-8 para o Excel. O HTML filtra, ordena e reordena colunas no navegador, sem nova consulta. Saúde 1–3 ruim, 4–7 regular, 8–10 boa; 0 inativo.

No terminal, o navegador abre o HTML. Na página, o resultado traz barra para nova consulta e download do CSV. Só uma consulta roda por vez.
