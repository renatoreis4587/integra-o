# Manual de Uso — Integra-O Sistema de Balanças

Este manual detalha a operação e a configuração do sistema.

---

## 1. Visão geral da arquitetura

```
┌────────────────────┐        RS232 / TCP        ┌─────────────────────────┐
│      BALANÇA       │ ◄───────────────────────► │   Servidor Python       │
│  (hardware físico) │                           │   (backend/scale)       │
└────────────────────┘                           │  • leitura contínua     │
                                                 │  • tara / zeragem       │
                                                 │  • estabilidade         │
                                                 └───────────┬─────────────┘
                                                             │ WebSocket + REST
                                                             ▼
                                                 ┌─────────────────────────┐
                                                 │  Navegador (frontend)   │
                                                 │  • painel de peso       │
                                                 │  • comandos e config    │
                                                 └───────────┬─────────────┘
                                                             │ fila assíncrona
                                                             ▼
                                       ┌───────────────────────────────────┐
                                       │  Nuvem: OneDrive / Google Drive / │
                                       │  WebDAV / pasta local             │
                                       └───────────────────────────────────┘
```

O **backend** roda na máquina local e conversa com a balança. O **frontend** é
uma página web servida pelo próprio backend, acessível por qualquer navegador.

---

## 2. Fluxo de pesagem

1. O sistema conecta na balança e lê continuamente.
2. O peso bruto é exibido no painel em tempo real.
3. Ao clicar em **Capturar Tara**, o peso atual vira tara; o **líquido** passa a
   ser `bruto − tara`.
4. Quando o peso fica estável por um tempo (configurável), o sistema **salva
   automaticamente** a pesagem na nuvem.
5. Também é possível salvar manualmente com **Salvar Pesagem**.

---

## 3. Comandos do painel

| Botão | Ação |
|---|---|
| **Capturar Tara** | Define a tara igual ao peso bruto atual. |
| **Tara Manual** | Permite digitar um valor de tara específico. |
| **Limpar Tara** | Zera a tara (volta ao peso bruto). |
| **Zerar Balança** | Ajusta o offset para o peso atual ser zero. |
| **Salvar Pesagem** | Registra a pesagem atual e envia para a nuvem. |
| **Conectar/Desconectar** | Liga/desliga a conexão com a balança. |

---

## 4. Configuração detalhada

### 4.1 Aba Conexão

**Tipo de Conexão**
- *RS232 (Serial)*: para balanças ligadas por cabo serial/adaptador USB-serial.
- *TCP/IP (Rede)*: para balanças com interface Ethernet/Wi-Fi.

**Serial**
- *Porta*: `COM1`, `COM3`... (Windows) ou `/dev/ttyUSB0`, `/dev/ttyS0` (Linux).
- *Baudrate*: normalmente 9600; algumas balanças usam 4800, 19200 ou 38400.
- *Data Bits / Paridade / Stop Bits*: geralmente `8-N-1`.

**TCP/IP**
- *Host/IP*: endereço da balança (ex.: `192.168.0.100`).
- *Porta*: porta do servidor TCP da balança (ex.: `4001`, `5000`, `9100`).
- *Timeout*: tempo máximo de espera por resposta.

**Reconexão automática**: reconecta sozinho após queda, com intervalo definido.

### 4.2 Aba Protocolo

- **Genérico**: extrai o primeiro número da linha. Ideal quando a balança envia
  algo como `  12.345\r\n`.
- **Mettler Toledo / Toledo**: formato `ST,GS,+  12.345 kg`.
- **Filizola**: formato numérico brasileiro.
- **CAS**: formato `ST,GS, 12.345kg`.
- **A&D (AND)**: formato `ST,+0012.345 kg`.
- **Personalizado (Regex)**: você define a expressão regular com o grupo
  nomeado `weight`. Exemplo:
  ```
  PESO=(?P<weight>[-+]?\d+[\.,]?\d*)\s*(?P<unit>kg|g)
  ```

**Casas decimais (implícitas)**: use quando a balança não envia o ponto decimal.
Ex.: se envia `12345` para representar `12,345`, use **3**.

**Multiplicador**: fator aplicado ao valor (ex.: `0.001` se vier em gramas).

**Terminador de linha**: `CR+LF`, `CR` ou `LF` — depende do equipamento.

### 4.3 Aba Pesagem

- **Unidade**: kg, g, t, lb.
- **Tara padrão**: tara aplicada automaticamente ao iniciar.
- **Limiar de estabilidade**: variação máxima para considerar o peso parado.
- **Tempo p/ estabilizar**: tempo que o peso deve ficar parado.
- **Peso mínimo válido**: evita salvar ruído próximo de zero.
- **Salvar automático**: liga/desliga o salvamento automático.
- **Intervalo entre salvamentos**: tempo mínimo entre dois salvamentos.

### 4.4 Aba Nuvem

Escolha o provedor e o formato. Para **pastas sincronizadas**, informe o
caminho local da pasta que o OneDrive/Google Drive sincroniza.

Exemplos de caminho:
- Windows OneDrive: `C:\Users\SeuUsuario\OneDrive\Pesagens`
- Windows Google Drive: `G:\Meu Drive\Pesagens`
- Linux: `/home/usuario/OneDrive/Pesagens`

### 4.5 Aba Sistema

- **Host do servidor**: `0.0.0.0` (toda a rede) ou `127.0.0.1` (só local).
- **Porta do servidor**: padrão 5000.
- **Nome da empresa** e **Operador**: aparecem no painel e nas pesagens.

> Alterações de host/porta exigem reiniciar o servidor.

---

## 5. Formato dos arquivos salvos

### CSV (padrão)
```csv
timestamp,weight,unit,tare,gross,net,stable,operator,scale
2026-09-28T20:00:00,10.999,kg,0.0,10.999,10.999,True,,TCP 127.0.0.1:4001
```

### JSON
```json
{"id":1,"timestamp":"2026-09-28T20:00:00","weight":10.999,"unit":"kg","tare":0.0,"gross":10.999,"net":10.999,"stable":true,"operator":"","scale":"TCP 127.0.0.1:4001"}
```

### TXT
```
2026-09-28T20:00:00	10.999
```

---

## 6. Acesso remoto pela rede

1. No servidor, mantenha o *Host* como `0.0.0.0`.
2. Descubra o IP: `ipconfig` (Windows) ou `ip a` (Linux).
3. Em outro PC, acesse `http://IP_DO_SERVIDOR:5000`.
4. Se não abrir, libere a porta no firewall:
   - Windows: Painel de Controle → Firewall → Regra de Entrada → Porta 5000.
   - Linux: `sudo ufw allow 5000`.

---

## 7. Perguntas frequentes

**O sistema funciona sem internet?**
Sim. A internet só é necessária para o envio à nuvem. O restante é local.

**Preciso de uma balança para testar?**
Não. Use `scripts/simulador_balanca.py`.

**Posso usar duas balanças ao mesmo tempo?**
Cada instância do sistema controla uma balança. Para duas balanças, rode duas
instâncias em portas diferentes.

**Onde ficam os dados salvos localmente?**
Na pasta definida em *Pasta local* (padrão: `backend/data`).

**Como faço backup das configurações?**
Copie o arquivo `config/config.json`.
