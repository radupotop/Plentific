## Context
You are joining a B2B work order management platform.
Field operatives complete work orders on a mobile app. 
Many operatives use the system concurrently during the workday.

We want to introduce a new Stock & Materials Management module to track materials held in:
* Vehicles (vans) assigned to 1–2 operatives
* Storage sites (e.g. warehouse, workshop)

Operatives must be able to record materials used on a work order.
Managers must be able to purchase, receive, transfer, adjust, audit, and reorder stock.

This module must integrate with existing platform concepts:
* WorkOrder (existing)
* Location (existing; represents properties/sites)

## Existing architecture
* The platform uses Django microservices.
* WorkOrders and Locations are managed by separate microservices.
* We deploy Django and Django Rest Framework with Gunicorn.
* PostgreSQL serves as the primary DBMS for Django.
* RabbitMQ acts as the Celery backend for asynchronous tasks.
* AWS SNS propagates events between microservices.

## Epic: "Materials visibility and usage tracking" - User stories
1. Record usage on a work order
* As a field operative, I want to record materials used (e.g. 3 screws, 2m
cable) from my van while completing a work order, so the job record is
accurate.
2. Manage stock in containers
* As an operations manager, I want to view current stock levels per vehicle and
storage site, and overall.
3. Receive stock
* As a store manager, I want to receive goods into a storage site (and optionally
allocate to vehicles).
4. Transfer stock
* As an operations manager, I want to transfer materials between vehicles and
storage sites.
5. Correct discrepancies
* As a manager, I want to adjust stock levels (with a reason).
6. Audit / stock take
* As a manager, I want to perform a stock take for a container and record
discrepancies.
7. Reorder
* As a manager, I want low stock alerts and the ability to create purchase
orders or reorder requests.

## Acceptance criteria
Candidates should interpret and expand these; this isn't exhaustive.
Materials usage
* Operative can add multiple line items to a work order usage record.
* Quantities must support tracking methods:
  ○ container-level (e.g. "1 box consumed")
  ○ unit-level (e.g. "12 screws consumed")
  ○ continuous (e.g. "2.4 metres consumed", with minimum increments)
* Must prevent negative stock beyond a defined tolerance (candidate to propose approach and assumptions).

## Stock levels
* Must support "on-hand" (and allow later extension for "reserved").
* Must answer queries:
* stock levels for all containers (per SKU)
* stock levels for one container at time T
* Must handle concurrent usage updates safely.

## Cost accounting (stretch)
* Support FIFO or LIFO for cost valuation where items are purchased in lots/batches at
different prices and consumed per unit.

## Performance
* Typical workday: hundreds of concurrent users, thousands of movements/day.
* Stock lookups must be fast enough for mobile UX.
* Historical reporting should not degrade OLTP performance.

## Design for evolution
The design should support likely future requirements without large refactors. Examples:
* reservations/allocations
* returns
* richer approvals/procurement lifecycle
* additional container types (e.g. lockers)

## Day-0 population (mandatory)
The design must consider how a new client begins using the module with existing stock
already in vans/sites:
* initial catalogue/catalogue items availability
* initial inventory quantities (possibly per container)
* optional initial valuation/cost layers
* validation, auditing, and reconciliation for initial load
* operationally feasible import process (e.g. CSV, API, admin tool)

## Candidate deliverables (mandatory)
Provide a written response (2–8 pages) plus diagrams. You may choose REST or
RPC.

1. Qualifying questions + assumptions
* We won't answer questions during the exercise.
* Write the questions you would ask and the assumptions you proceed with
(briefly), including risks.

2. Proposed architecture
* How this module fits into an existing backend
* Boundaries: services/modules, responsibilities
* Transaction boundaries, idempotency, failure handling
* Design choices that enable evolution without rewrites

3. Data model package (MANDATORY)

Provide both:
A) Entity Relationship Diagram (ERD)
* Must be included (Mermaid is fine)
* Should cover core entities and key relationships
* Show cardinalities

B) Field-level descriptions

For each entity/table, list:
* fields, types, nullability
* what the field means and how it's used
* key constraints and indexes you recommend
* important invariants (e.g. non-negative rules, unique keys)

Also explain trade-offs between ledger-only vs persisted balances vs materialised views/snapshots.

4. API design
Design a small set of endpoints, e.g.:
* record usage on a work order
* receive stock
* transfer stock
* adjust stock
* query stock levels (container + global)
* day-0 import / initialisation flows (could be async)
Include:
request/response shapes
* validation rules
* idempotency strategy (mobile retries)
* error semantics (what clients receive on conflicts)

5. Migration and rollout plan
Assume:
* WorkOrders and Locations already exist
* module ships incrementally behind feature flags
Cover:
* schema migration approach (safe, reversible)
* any backfills required
* rollout plan with monitoring
* day-0 onboarding steps and operational tooling required

6. Non-functional considerations
Explicitly cover:
* concurrency control strategy (locking, optimistic concurrency, isolation)
* scalability (indexes, partitioning, read models)
* reliability (retries, idempotency, outbox/events where needed)
* observability (metrics, invariants, alerts)

7. Phase plan (nice-to-have)
MVP → v2 plan, including what you would defer while keeping an evolutionary path.
