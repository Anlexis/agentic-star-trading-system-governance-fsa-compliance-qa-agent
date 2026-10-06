# Trading System Governance & FSA Compliance Q&A Agent

AI agent for answering governance and FSA compliance questions about multi-agent trading systems, built with Agentic Star.

> **Category**: Cat 2 (domain-specific RAG pipeline)
> **Industry**: Finance
> **Template ID**: FIN-C2-108

## Overview

Answers trading-system governance questions from a seeded regulatory knowledge
base and returns a cited answer plus the structured compliance data a review
team needs next. Financial-institution IT, compliance and internal-audit staff
ask a natural-language question about governing an AI/algorithmic trading
system — model-validation duties, circuit-breaker requirements, audit-trail
retention, security-control mappings — and the agent retrieves the relevant
passages from a seeded knowledge base covering financial-regulator AI
model-risk-management guidance, the algorithmic-trading provisions of Japan's
Financial Instruments and Exchange Act (金融商品取引法), and the FISC security
guidelines, then assembles a grounded answer with numbered citations.

Alongside the rendered answer, every successful run emits four structured
compliance deliverables built only from the retrieved passages: a
model-risk-management checklist, the applicable circuit-breaker parameters, an
audit trail of the run, and a security-control mapping. The pipeline is fully
deterministic and network-free — keyword retrieval and rule-based assembly
over the local knowledge base, no live model call — and every answer carries a
mandatory, non-suppressible advisory disclaimer: the output is informational,
not legal or compliance advice. An output content gate scans the rendered
answer and every structured field (recursively) before anything is surfaced.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails
during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/` for the design document and the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
