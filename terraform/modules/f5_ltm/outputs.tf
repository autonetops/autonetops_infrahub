output "device" {
  description = "Load balancer the config was rendered for (name, cluster, management_address, os_version)."
  value       = local.cfg.device
}

output "managed_objects" {
  description = "Full paths of the LTM objects this module manages, by type."
  value = {
    monitors        = keys(bigip_ltm_monitor.this)
    nodes           = keys(bigip_ltm_node.this)
    pools           = keys(bigip_ltm_pool.this)
    pool_members    = keys(bigip_ltm_pool_attachment.this)
    snatpools       = keys(bigip_ltm_snatpool.this)
    virtual_servers = keys(bigip_ltm_virtual_server.this)
  }
}

# Import IDs per resource address, for adopting objects that already exist on
# the box (see deployments/lb-dc2, adopt_existing).
output "import_ids" {
  description = "bigip provider import ID per for_each key, by resource type."
  value = {
    monitors        = { for k, _ in local.monitors : k => k }
    nodes           = { for k, _ in local.nodes : k => k }
    pools           = { for k, _ in local.pools : k => k }
    pool_members    = { for k, m in local.pool_members : k => jsonencode({ pool = m.pool, node = m.node }) }
    snatpools       = { for k, _ in local.snatpools : k => k }
    virtual_servers = { for k, _ in local.virtual_servers : k => k }
  }
}
