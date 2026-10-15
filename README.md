# Return Detective Agent — ALDO Group

An AI-powered agentic system that investigates, analyzes, and resolves customer return requests for the ALDO Group. The agent applies business rules, purchase history, and return policy logic to make intelligent decisions — reducing manual effort and improving the customer experience.

---

## Overview

The Return Detective Agent acts as a first-line investigator for return and refund requests across ALDO Group brands (ALDO, Call It Spring, GLOBO). It gathers context from relevant systems, evaluates eligibility, flags anomalies, and either resolves cases autonomously or routes them to a human agent with a full dossier.

---

## Features

- **Automated return eligibility checks** — validates return windows, receipt/proof of purchase, and item condition against current policy
- **Order & transaction lookup** — queries order management and POS systems to retrieve purchase details
- **Fraud and anomaly detection** — identifies suspicious return patterns (e.g. high return frequency, wardrobing, missing tags)
- **Multi-channel support** — handles returns initiated in-store, online, or via customer service
- **Policy-aware reasoning** — applies brand-specific rules for ALDO, Call It Spring, and GLOBO
- **Escalation routing** — hands off complex or high-risk cases to human agents with a structured summary
- **Audit trail** — logs every decision and reasoning step for compliance and review

---

## Architecture

```
Customer Request
      │
      ▼
┌─────────────────────┐
│  Return Detective   │  ← Orchestrator Agent
│      Agent          │
└────────┬────────────┘
         │
    ┌────┴─────┐
    ▼          ▼
Order       Policy
Lookup      Engine
Tool        Tool
    │          │
    └────┬─────┘
         ▼
  Fraud Detection
      Module
         │
         ▼
  Decision Engine
  ┌──────┴──────┐
  ▼             ▼
Auto-       Escalate to
Resolve     Human Agent
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- AWS account with appropriate IAM permissions
- Access to ALDO Group internal APIs (order management, POS, CRM)

### Installation

```bash
git clone https://github.com/aldo-group/return-detective-agent.git
cd return-detective-agent
pip install -r requirements.txt
```

### Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Key environment variables:

| Variable | Description |
|---|---|
| `AWS_REGION` | AWS region for deployed services |
| `ORDER_API_URL` | Endpoint for the order management system |
| `CRM_API_URL` | Endpoint for the customer CRM |
| `POLICY_VERSION` | Active return policy version to apply |
| `FRAUD_THRESHOLD` | Risk score threshold for escalation (0–1) |

### Running the Agent

```bash
python main.py
```

---

## Return Decision Logic

The agent follows a structured investigation flow:

1. **Identify** — retrieve the order, customer profile, and return request details
2. **Validate** — check return window, item eligibility, and proof of purchase
3. **Assess** — score the request for fraud risk using behavioral signals
4. **Decide** — approve, deny, or escalate based on policy rules and risk score
5. **Act** — initiate refund, exchange, or store credit; or route to a human agent

---

## Escalation Criteria

Cases are escalated to a human agent when any of the following are true:

- Fraud risk score exceeds the configured threshold
- Return value exceeds the autonomous approval limit
- Customer has an open dispute or chargeback
- Item is flagged as non-returnable (final sale, customized, etc.)
- Insufficient purchase evidence to make a confident decision

---

## Project Structure

```
return-detective-agent/
├── main.py               # Entry point
├── agent/
│   ├── orchestrator.py   # Main agent loop
│   ├── tools/            # Order lookup, policy engine, fraud detection
│   └── prompts/          # System and task prompts
├── config/
│   └── policy.yaml       # Return policy rules per brand
├── tests/                # Unit and integration tests
├── .env.example          # Environment variable template
└── README.md
```

---

## Contributing

Internal contributions follow the ALDO Group engineering guidelines. Open a pull request against `main` with a clear description of the change and any relevant test coverage.

---

## License

Internal use only — © ALDO Group. All rights reserved.
