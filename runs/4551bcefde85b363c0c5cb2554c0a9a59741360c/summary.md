## Skill evaluation: FAIL / INCOMPLETE

Selection: local — aws-drawio-diagram, essr-html-report, folder-specific-claude-and-agents-md, visual-flow-webp · 17 scored cases · 3 failing gate rows

| Skill | Case | Overall | Status | Baseline |
| --- | --- | ---: | --- | --- |
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
- essr-html-report: not provided
- folder-specific-claude-and-agents-md: not provided
- visual-flow-webp: exit code 0

Download the `skill-evaluation` artifact and open `.eval/dashboard.html` for evidence links.
