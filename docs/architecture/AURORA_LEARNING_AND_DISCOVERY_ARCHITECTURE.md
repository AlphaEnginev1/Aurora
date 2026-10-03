# Aurora Learning Loop + Discovery Loop

**Status:** FUTURE TARGET ARCHITECTURE / REFERENCE  
**Canonical runtime:** `main`  
**Purpose:** Preserve the intended end-state for Aurora's autonomous learning and novel-discovery capabilities. This document is architectural guidance; it does not claim that the complete discovery loop is implemented yet.

## 1. Core principle

Aurora must not be limited to retrieving and repeating information found on the Internet.

Internet research is an evidence input. Aurora's higher-order objective is to use that evidence to identify gaps, contradictions and opportunities, formulate hypotheses, design investigations, run controlled experiments or simulations, attempt falsification, reproduce promising results, and promote only independently supported discoveries into durable Aurora knowledge.

## 2. Two complementary loops

Aurora should operate two distinct but connected learning loops.

### Learning Loop

```
EXECUTION
   ↓
OBSERVATION
   ↓
EVALUATION
   ↓
LEARNING
   ↓
ADAPTATION
   ↓
NEXT EXECUTION
```

The Learning Loop improves existing behavior, skills, agent selection, model routing and decision quality from observed outcomes.

### Discovery Loop

```
INTERNET + INTERNAL KNOWLEDGE
            ↓
     KNOWLEDGE LANDSCAPE
            ↓
   GAPS / CONTRADICTIONS
            ↓
       HYPOTHESES
            ↓
   EXPERIMENT DESIGN
            ↓
  SIMULATION / COMPUTATION /
      EXTERNAL TEST
            ↓
        NEW RESULT
            ↓
   INDEPENDENT VALIDATION
            ↓
      NOVELTY / PRIOR-ART
           CHECK
            ↓
    NEW VERIFIED KNOWLEDGE
            ↓
     KNOWLEDGE GRAPH
            ↓
          LEARNING
            ↓
       NEXT RESEARCH
```

The Discovery Loop must be able to produce a result whose novelty is established by Aurora's own investigation rather than merely by finding a sentence on an existing website.

## 3. Combined Aurora architecture

```
                       DIRECTOR
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
        LEARNING LOOP             DISCOVERY LOOP
              │                         │
         execute/evaluate          research
              │                         │
          adapt/router             hypothesize
              │                         │
              └────────────┬────────────┘
                           ▼
                     RSI / META-LOOP
                           │
                           ▼
              Improve how Aurora learns,
                 investigates and validates
                           │
                           ▼
                       NEXT CYCLE
```

## 4. Research is not the final authority

Internet retrieval must remain evidence acquisition, not truth by default.

Aurora should preserve:

- immutable source snapshots and provenance;
- claims separated from Aurora inference;
- independent verification;
- contradiction handling;
- citation/provenance graph;
- uncertainty and missing-evidence states.

A source-backed claim and an Aurora-generated hypothesis must remain different evidence classes.

## 5. Requirements for genuine novel discovery

A future Discovery Controller should persist first-class records for:

- research questions;
- knowledge gaps;
- hypotheses and competing hypotheses;
- experiment plans;
- expected observations;
- generated results;
- falsification attempts;
- independent replications;
- prior-art and novelty checks;
- discovery confidence;
- evidence completeness;
- promotion/rejection decisions.

The controller should distinguish:

**known:** directly supported by existing evidence;

**inferred:** synthesized from known evidence but not independently demonstrated;

**hypothesis:** proposed explanation or opportunity awaiting testing;

**discovered:** supported by Aurora's own reproducible investigation and survives the required independent validation and novelty checks.

## 6. Discovery quality gate

A candidate discovery must not become trusted knowledge merely because an LLM proposes it or because it is absent from the first search results.

The target gate is:

```
Hypothesis
   ↓
Controlled investigation
   ↓
Observed result
   ↓
Independent verifier
   ↓
Falsification / counterexample search
   ↓
Prior-art / novelty analysis
   ↓
Reproduction where feasible
   ↓
Trusted discovery
```

The evaluator must be independent from the generator whenever practical, and the system should retain failed hypotheses and dead ends so future cycles do not repeatedly explore disproven paths.

## 7. Relationship to RSI

The RSI / Meta-Loop sits above the Learning Loop and Discovery Loop.

It should improve not only the object-level skills and strategies, but also the process used to improve and investigate:

```
Skills
  ↓
Routing
  ↓
Experiment strategy
  ↓
Evaluation strategy
  ↓
Research strategy
  ↓
Discovery strategy
  ↓
Improvement strategy
  ↓
Next RSI generation
```

This is the architectural target for bounded, governed recursive self-improvement.

## 8. Current implementation boundary

Aurora already contains important foundations for this target, including:

- InternetGateway and research discovery;
- HttpSearchProvider;
- ResearchService;
- source/claim/evidence persistence;
- independent verification and contradiction handling;
- trusted knowledge artifacts and citation graph;
- discovery department;
- hypothesis-generation Skill;
- novelty-analysis Skill;
- synthesis and verification capabilities;
- Learning Loop components for skills, models, routing and Director decisions.

The complete autonomous Discovery Loop is **not yet claimed complete** by this document. The missing objective is the first-class closed cycle from knowledge gaps → hypotheses → autonomous investigation/experiments → independently verified novel result → trusted new knowledge → next research cycle.

## 9. Safety and governance

The Discovery Loop must remain bounded and auditable.

Required controls include:

- explicit resource and execution budgets;
- sandboxed experimentation;
- provenance for every input and result;
- independent verification;
- hold-out validation where appropriate;
- rollback of derived artifacts;
- kill switch and policy boundaries;
- no silent promotion of model text to authoritative knowledge;
- preservation of uncertainty when evidence is insufficient.

The goal is not to make Aurora believe more things. The goal is to make Aurora capable of discovering more things while becoming progressively better at determining what it actually knows.

## 10. Definition of done for this future architecture

The Discovery Loop will be considered fully implemented only when Aurora can demonstrate an end-to-end, reproducible case in which:

1. it starts from existing evidence;
2. identifies a meaningful knowledge gap or contradiction;
3. generates competing hypotheses;
4. designs and executes an investigation;
5. obtains a result not simply copied from a source;
6. performs independent validation and falsification attempts;
7. checks relevant prior art/novelty;
8. persists the resulting provenance and evidence;
9. promotes the result to trusted knowledge only when its evidence gate is satisfied;
10. uses that newly acquired knowledge in a later Aurora cycle.
