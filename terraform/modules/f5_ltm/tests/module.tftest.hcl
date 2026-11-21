# Offline tests (no BIG-IP): `terraform test` in terraform/modules/f5_ltm.
# The provider is mocked, so these check the module's mapping only; the live
# behaviour is covered by tests/live_test.sh.

mock_provider "bigip" {}

variables {
  config = file("tests/fixtures/tftest-v1.yml")
}

run "maps_every_object" {
  command = plan

  assert {
    condition     = length(bigip_ltm_virtual_server.this) == 2 && length(bigip_ltm_pool.this) == 2
    error_message = "expected 2 virtuals and 2 pools"
  }
  assert {
    condition = toset(keys(bigip_ltm_pool_attachment.this)) == toset([
      "/Common/tftest-web|/Common/198.18.0.10:80",
      "/Common/tftest-web|/Common/198.18.0.11:80",
      "/Common/tftest-l4|/Common/198.18.0.10:0",
    ])
    error_message = "pool members are flattened as <pool>|<member>"
  }
  assert {
    condition     = bigip_ltm_pool_attachment.this["/Common/tftest-web|/Common/198.18.0.11:80"].state == "disabled"
    error_message = "member state not mapped"
  }
  assert {
    condition     = bigip_ltm_virtual_server.this["/Common/tftest-vs-l4"].snatpool == "/Common/tftest-snat"
    error_message = "SNAT pool not mapped"
  }
  assert {
    condition     = bigip_ltm_virtual_server.this["/Common/tftest-vs-https"].client_profiles == toset(["/Common/clientssl"])
    error_message = "client-side profiles not mapped"
  }
}

run "empty_config_manages_nothing" {
  command = plan
  variables {
    config = file("tests/fixtures/empty.yml")
  }
  assert {
    condition     = length(bigip_ltm_pool_attachment.this) == 0 && length(bigip_ltm_virtual_server.this) == 0
    error_message = "an empty document must manage nothing"
  }
}

run "rejects_unknown_format" {
  command = plan
  variables {
    config = "format_version: 2\n"
  }
  expect_failures = [var.config]
}

run "rejects_config_for_another_box" {
  command = plan
  variables {
    config        = replace(file("tests/fixtures/empty.yml"), "management_address: null", "management_address: 10.10.10.121")
    bigip_address = "10.10.10.111"
  }
  expect_failures = [var.bigip_address]
}
