output "deployment_name" {
  value = kubernetes_deployment.app.metadata[0].name
}

output "service_endpoint" {
  value = "${kubernetes_service.app.metadata[0].name}.${var.namespace}.svc.cluster.local:${var.service_port}"
}

output "external_url" {
  value = "https://${var.agent_name}.${var.domain}"
}
