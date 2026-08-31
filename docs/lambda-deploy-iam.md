# IAM grant required by `.github/workflows/aws-lambda-jobs.yml`

The CI role `ccit-dev-energycosmos-deploy` carries ECR, EC2, SSM, S3, SQS,
DynamoDB and IAM access, but **no `lambda:*` permission of any kind**. The
Lambda rollout step therefore fails with `AccessDenied` until a policy granting
it is attached.

Scope it to the one function on purpose — the role already has more breadth
than this pipeline needs, and widening it further to fix a deploy is how a CI
role quietly becomes an admin role.

## The grant

Attach an inline policy named `tradepro-lambda-deploy` to the role
`ccit-dev-energycosmos-deploy`, using the `infoccit-admin` profile, with this
document:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "UpdateAndSmokeTestTheJobsFunction",
      "Effect": "Allow",
      "Action": [
        "lambda:UpdateFunctionCode",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:PublishVersion",
        "lambda:InvokeFunction"
      ],
      "Resource": "arn:aws:lambda:eu-west-2:108703420282:function:tradepro-jobs"
    },
    {
      "Sid": "ReadScheduleNamesForTheDeploySummary",
      "Effect": "Allow",
      "Action": ["events:ListRules"],
      "Resource": "*"
    }
  ]
}
```

Then re-run the workflow with `gh workflow run aws-lambda-jobs`.

## Why this matters more than it looks

`tradepro-post-earnings-puts` runs at 20:45 UTC Mon–Fri and **pushes to the
live desk artifact**. A stale image does not fail quietly — it overwrites a
good board with a worse one, in the strategy's voice rather than the
deployment's.

On 31 Aug 2026 the deployed image predated both the `maxStrikes=1` expiry
discovery fix (9d15ad9) and the 5s quote-poll spacing (292eff3) — the exact two
bugs that stop the screen pricing. Every weeknight run would have refused to
price while `main` looked correct.
