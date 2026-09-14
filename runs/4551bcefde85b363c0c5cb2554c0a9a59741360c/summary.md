## Skill evaluation: FAIL / INCOMPLETE

Selection: local — aws-drawio-diagram, essr-html-report, folder-specific-claude-and-agents-md, visual-flow-webp · 30 scored cases · 13 failing gate rows

| Skill | Case | Overall | Status | Baseline |
| --- | --- | ---: | --- | --- |
| visual-flow-webp | flow-webp-git-webhook-dark | 0.958 | PASS | +0.005; legacy profile unknown; rubric changed |
| visual-flow-webp | flow-webp-git-webhook-from-readme | 0.958 | PASS | -0.025; legacy profile unknown; rubric changed |
| visual-flow-webp | flow-webp-git-webhook-stepwise | 0.917 | FAIL | -0.036; legacy profile unknown; rubric changed |
| visual-flow-webp | flow-webp-git-webhook-extend | 0.917 | FAIL | -0.040; legacy profile unknown; rubric changed |
| visual-flow-webp | flow-webp-git-webhook-slide-mp4 | 1.000 | PASS | +0.067; legacy profile unknown; rubric changed |
| visual-flow-webp | flow-webp-reference-overlay-mp4 | 1.000 | PASS | No baseline |
| aws-drawio-diagram | coinbase-low-latency | 1.000 | PASS | No baseline |
| aws-drawio-diagram | three-tier-web | 0.917 | FAIL | No baseline |
| aws-drawio-diagram | adversarial-inline-xml | 1.000 | PASS | No baseline |
| aws-drawio-diagram | log-pipeline-streaming | 0.958 | FAIL | No baseline |
| aws-drawio-diagram | drawio-diagram-only-reply | 1.000 | PASS | No baseline |
| folder-specific-claude-and-agents-md | claude-md-ingest-folder | 0.960 | FAIL | No baseline |
| folder-specific-claude-and-agents-md | claude-md-subdir-under-root | 0.900 | FAIL | No baseline |
| aws-drawio-diagram | coinbase-low-latency | 1.000 | PASS | No baseline |
| aws-drawio-diagram | three-tier-web | 1.000 | PASS | No baseline |
| aws-drawio-diagram | adversarial-inline-xml | 1.000 | PASS | No baseline |
| aws-drawio-diagram | log-pipeline-streaming | 1.000 | PASS | No baseline |
| aws-drawio-diagram | drawio-diagram-only-reply | 1.000 | PASS | No baseline |
| essr-html-report | essr-baseline-report | 0.958 | FAIL | No baseline |
| essr-html-report | essr-exception-both-scores | 1.000 | PASS | No baseline |
| essr-html-report | essr-s3-severity-narrowing | 0.958 | FAIL | No baseline |
| essr-html-report | essr-adversarial-handwrite | 1.000 | PASS | No baseline |
| folder-specific-claude-and-agents-md | claude-md-ingest-folder | 1.000 | PASS | No baseline |
| folder-specific-claude-and-agents-md | claude-md-subdir-under-root | 0.950 | FAIL | No baseline |
| visual-flow-webp | flow-webp-git-webhook-dark | 1.000 | PASS | +0.047; legacy profile unknown; rubric changed |
| visual-flow-webp | flow-webp-git-webhook-from-readme | 1.000 | PASS | +0.017; legacy profile unknown; rubric changed |
| visual-flow-webp | flow-webp-git-webhook-stepwise | 1.000 | PASS | +0.047; legacy profile unknown; rubric changed |
| visual-flow-webp | flow-webp-git-webhook-extend | 1.000 | PASS | +0.043; legacy profile unknown; rubric changed |
| visual-flow-webp | flow-webp-git-webhook-slide-mp4 | 1.000 | PASS | +0.067; legacy profile unknown; rubric changed |
| visual-flow-webp | flow-webp-reference-overlay-mp4 | 1.000 | PASS | No baseline |

<details><summary>flow-webp-git-webhook-stepwise — RecordedExecution</summary>

```
recorded execution or deterministic checks failed or are missing
```
</details>

<details><summary>flow-webp-git-webhook-stepwise — Assertions[#1]</summary>

```
#1 FAILS: The spec animates with `animation.steps`, not with `animation.pulses`/`paths` alone, and the steps are exactly four in this order: the users-to-repository edge; the repository-to-Lambda edge; the three Lambda-to-store edges together in one step; the Lambda-to-repository return edge. No `pulses` list is set alongside `steps`.
   evidence: The spec's `animation` object has `steps` (no `pulses`, satisfying that clause), but it contains 5 steps, not 4: step 0 (edge 0: user→git-repo), step 1 (edge 1: git-repo→lambda), step 2 (edges 2,3,4: lambda→three stores), step 3 (edge 5: lambda→git-repo), and step 4 (edge 6: git-repo→user, 'Loop back'). The assertion requires exactly four steps; the fifth step (git-repo→user loop-back) is present in the spec and confirmed by the lint output: 'spec lint: 12 nodes, 7 edges, 5 steps'.
```
</details>

<details><summary>flow-webp-git-webhook-stepwise — Assertions[#3]</summary>

```
#3 FAILS: The agent ran `lint_spec.py` before rendering and rendered with `--verify --check`; the renderer's `✓ PASS` line reports 4 steps and a loop length, and the reply quotes that loop length (in seconds) rather than describing the pace impressionistically. Nothing in the reply claims a step order or timing beyond what the lint, the `✓ PASS` line and the `steps_confined` check established.
   evidence: The assertion requires the PASS line to report '4 steps'. The actual PASS line reads: '✓ PASS git-webhook-aws.png + git-webhook-aws.webp 1400x800 frames=175 9.5s (5 steps) icons=5 motion=5/5 sampled pairs' — it reports 5 steps, not 4. The agent ran lint before rendering (tooluse_3/5 before tooluse_7) and used `--verify --check` ✓, and the reply does quote the 9.5 s loop length ✓, but the '4 steps' sub-clause fails.
```
</details>

<details><summary>flow-webp-git-webhook-extend — Assertions[#1]</summary>

```
#1 FAILS: The agent read the existing git-webhook.spec.json and edited it rather than writing a new spec from scratch: the original node ids (users, repo, lambda, ssh, kms, out, cloud), the coordinates of the six foreground nodes, the dark theme and the title are unchanged in the delivered spec, and the seed's four steps still travel the same edges in the same order. Growing the canvas and the `cloud` container to make room for the new node is fine; moving the existing nodes is not. Reading the skill's bundled asset specs was not needed and is not held against the run either way.
   evidence: The assertion requires that the coordinates of the six foreground nodes (users, repo, lambda, ssh, kms, out) are unchanged from the seed. In the delivered spec, `out` (Output Bucket) is at x=680, y=530. However, the agent's own reply states it 'Repositioned Output Bucket from x=1060 to x=680 to make room for the new flow' -- meaning the `out` node was moved. The delivered spec confirms out is at x=680, y=530, w=240, h=100, which differs from the seed's position. Moving existing nodes is explicitly not allowed per the assertion.
```
</details>

<details><summary>three-tier-web — Assertions[#1]</summary>

```
#1 FAILS: Public-facing components sit in a public subnet and the database sits in a private subnet.
   evidence: In the spec, CloudFront is in group 'region' (not a public subnet), and the ALB is in group 'vpc' (not a public subnet either). The nodes placed in public subnets are: none — 'public_subnet_a' and 'public_subnet_b' are empty (confirmed by validator warning: 'container(s) with nothing in them, which render as dead space: public_subnet_a, public_subnet_b'). The database (rds_primary, rds_standby) does sit in 'data_subnet_a' and 'data_subnet_b' which are 'subnet_private' kind — that part holds. But the public-facing components (CloudFront, ALB) are NOT in public subnets.
```
</details>

<details><summary>log-pipeline-streaming — Assertions[#4]</summary>

```
#4 FAILS: The annotations carry design rationale — why a shared file system precedes the shipper, or what the alternative to Flink would be — not a restatement of what the icons already show.
   evidence: Annotation 1: "Applications write logs to Amazon FSx for OpenZFS, a shared, high-performance file system accessible across multiple Amazon EC2 instances with NFS protocol support and snapshot capabilities." — this describes properties but doesn't explain WHY it precedes the shipper or what alternative was considered. Annotation 4: "Amazon Managed Service for Apache Flink consumes log streams from MSK in real time, performing filtering, aggregation, and enrichment before loading results into the data warehouse." — this restates what Flink does without mentioning alternatives. The annotations largely describe what the components do (restating the picture with added adjectives) rather than answering 'why this component here' or 'what could go here instead', as required by the style guide's own test.
```
</details>

<details><summary>claude-md-ingest-folder — Assertions[#3]</summary>

```
#3 FAILS: The file contains no directory tree, no file listing for its own sake, and nothing an agent could derive by running LS or grep in the folder.
   evidence: The Essential Files section lists: '- `handler.py` — Lambda entrypoint with batch size hard cap', '- `normalize_cloudtrail.py` — field mapping for CloudTrail records (one module per source pattern)', '- `schema.json` — Avro schema for normalized output', '- `@notes.md` — dated decisions and context'. This is effectively a file listing of the folder's contents — each entry names a file that exists in the folder. While each has a one-line role description, the list of filenames is derivable directly by running LS in the folder, which the assertion says should not be present.
```
</details>

<details><summary>claude-md-subdir-under-root — RecordedExecution</summary>

```
recorded execution or deterministic checks failed or are missing
```
</details>

<details><summary>claude-md-subdir-under-root — Assertions[#3]</summary>

```
#3 FAILS: runbook.md is referenced with @-import syntax and annotated with a **Read when:** trigger rather than being summarized inline or left to load every session.
   evidence: The file uses '`@runbook.md` — **Read before:** touching cluster_version, changing node groups, or when asked to "upgrade EKS".' The trigger annotation says '**Read before:**' not '**Read when:**' as the assertion requires. The assertion explicitly states 'annotated with a **Read when:** trigger rather than being summarized inline or left to load every session.'
```
</details>

<details><summary>claude-md-subdir-under-root — Assertions[#4]</summary>

```
#4 FAILS: The spot-batch drain-never-cordon rule appears as a constraint, traced to cluster.tf rather than stated as generic Kubernetes advice.
   evidence: The constraint reads: '**MUST NOT cordon spot-batch nodes without draining them.** A cordoned spot node still counts against the ASG and blocks scale-up.' The node group name 'spot-batch' (or 'spot_batch') is specific to this cluster's configuration. However, the rule is not explicitly traced to cluster.tf — there is no citation, reference, or link to cluster.tf in the constraint or anywhere near it. The assertion requires it be 'traced to cluster.tf rather than stated as generic Kubernetes advice,' and no such trace appears.
```
</details>

<details><summary>essr-baseline-report — Assertions[#2]</summary>

```
#2 FAILS: The four checks whose `Check Status` is error are treated as having failed to evaluate rather than as passing or as failing findings, and the reply says which of them needs a manual look.
   evidence: The generator output and HTML show multiple checks with Check Status=error (IAM Access Key Rotation, MFA on root user, Security Groups - Specific Ports Unrestricted, CloudTrail Management Event Logging). The reply explicitly treats MFA on root user as unknown/unevaluated and calls for a manual look at beta-dev. However, the reply does not enumerate all four error-status checks, does not state that all four failed to evaluate rather than pass or fail, and does not say which of the remaining three need a manual look. The assertion requires all four to be treated as failed-to-evaluate and the reply to identify which need manual attention — only one (MFA on root user) receives that treatment explicitly.
```
</details>

<details><summary>essr-s3-severity-narrowing — Assertions[#4]</summary>

```
#4 FAILS: A deep link into findings.html is given that lands on the rows the number refers to, so the CISO figure is checkable rather than asserted.
   evidence: The final reply text contains no deep link URL to findings.html. The report HTML itself (ESSR-Acme-report.html) contains links like '<a href="ESSR-Acme-findings.html?check=S3%20Bucket%20Permissions&status=error">只看 7 条 Red</a>' but there is no link scoped to the 2-bucket answer (e.g., ?status=error&check=S3+Bucket+Permissions filtered further to bare 'Yes'). More critically, the agent's final reply to the user — the place where the CISO number is stated — contains no clickable deep link at all.
```
</details>

<details><summary>claude-md-subdir-under-root — Assertions[#4]</summary>

```
#4 FAILS: The spot-batch drain-never-cordon rule appears as a constraint, traced to cluster.tf rather than stated as generic Kubernetes advice.
   evidence: The constraint reads: 'MUST NOT cordon a `spot_batch` node and leave it. Drain it. A cordoned spot node still counts against the ASG and blocks the next scale-up.' The rule is present as a constraint, but there is no trace to cluster.tf. The node group name `spot_batch` does appear in cluster.tf, but the constraint text contains no citation of cluster.tf as its source. The assertion requires it be 'traced to cluster.tf rather than stated as generic Kubernetes advice,' and no such trace is present.
```
</details>

**Negative controls**

- aws-drawio-diagram: not provided
- folder-specific-claude-and-agents-md: not provided
- visual-flow-webp: exit code 0
- aws-drawio-diagram: not provided
- essr-html-report: not provided
- folder-specific-claude-and-agents-md: not provided
- visual-flow-webp: exit code 0

Download the `skill-evaluation` artifact and open `.eval/dashboard.html` for evidence links.
