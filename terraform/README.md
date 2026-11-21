# Terraform delivery of Infrahub-rendered config

## F5 BIG-IP LTM (`modules/f5_ltm`, `deployments/lb-dc2`)

```
Infrahub (LB pools / VIPs / SNAT pools on the device's cluster)
  └─ query f5_config + transforms/f5_config.py
       └─ artifact f5-ltm-configuration (application/yaml, one per DcimLoadBalancer
          in group load_balancers)
            └─ deployments/<device>  (fetches the artifact, connects to the box)
                 └─ modules/f5_ltm   (yamldecode → bigip_ltm_* resources)
```

The YAML speaks the F5Networks/bigip provider's vocabulary. The transform
makes every vendor decision (profile defaults, SNAT type, member naming), and
the module only maps fields. Every collection is a map keyed by the object's
full path (`/Common/name`), which is both the F5 name and the `for_each` key.
`format_version` versions the contract; the module rejects any other version.

**Ownership.** Terraform manages exactly the objects listed in the YAML.
Removing an object in Infrahub **deletes** it from the box on the next apply.
Objects Infrahub never listed (built-in profiles and monitors, anything made by
hand) are left alone. Node descriptions are not modelled in Infrahub and are
ignored.

**Provider quirks handled in the module** (both found by the live test):
- bigip 1.29 silently skips zero or empty values. A virtual's `connection_limit`,
  `vlans` or `description`, or a member's `priority_group` or `connection_limit`,
  dropping to 0, [] or null never reached the box. The module writes those
  fields with a `bigip_command` (tmsh `modify`). It runs only when the set of
  zeroed fields changes.
- A pool with no monitors is read back as `[""]`, so the module sends `[""]`.
  That value also clears a monitor the box has.

### Apply

```sh
cd deployments/lb-dc2
cp secret.tfvars.example secret.tfvars        # BIG-IP + Infrahub credentials, git-ignored
terraform init
terraform plan  -var-file=secret.tfvars       # pulls the Ready artifact from Infrahub main
terraform apply -var-file=secret.tfvars
```

- `-var config_file=lb-dc2.yml` applies a local render instead, for example
  `infrahubctl transform f5_config device=LB-DC2 --branch <branch> > lb-dc2.yml`.
  Use it to preview an Infrahub branch before it is merged.
- `-var adopt_existing=true` is for the **first** apply on a box that already
  has the objects. Every object is imported into state instead of being
  created. Leave it off afterwards: an import of an object the box lacks fails
  the plan.
- The module refuses a document whose `device.management_address` differs from
  `bigip_address`, so one box's config cannot be pushed to another.

State is local (`terraform.tfstate`, git-ignored). Move it to a shared backend
before a second operator or a pipeline applies.

### Tests

| what | how | needs |
|---|---|---|
| transform output and contract | `pytest tests/test_f5_config.py` (repo root) | nothing |
| module mapping | `terraform test` in `modules/f5_ltm` (mocked provider) | nothing |
| live create / day-2 edit / delete | `modules/f5_ltm/tests/live_test.sh` | a BIG-IP |
| box matches a YAML | `modules/f5_ltm/tests/verify.py <yml>` | a BIG-IP |

`live_test.sh` uses only `tftest-*` objects on 198.18.0.0/15 (benchmarking
space), with every virtual disabled, and destroys them at the end. It is safe
to run against a production box. After each apply it checks the box
field-by-field over REST and requires an empty re-plan.
