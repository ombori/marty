# Generic Agent Deployment
# All values derived from agent_name and domain variables

locals {
  labels = {
    "app.kubernetes.io/name"       = var.agent_name
    "app.kubernetes.io/managed-by" = "terraform"
  }
  # Registry hostname derived from domain
  registry_hostname = "registry.${var.domain}"
  # Internal registry for image pull
  internal_registry = "zot.registry.svc.cluster.local:5000"
}

# Wise API credentials secret (only created if wise_private_key is provided)
resource "kubernetes_secret" "wise_credentials" {
  count = var.wise_private_key != "" ? 1 : 0

  metadata {
    name      = "${var.agent_name}-wise-credentials"
    namespace = var.namespace
    labels    = local.labels
  }

  data = merge(
    {
      "wise_private.pem" = base64decode(var.wise_private_key)
      "WISE_API_TOKEN"   = var.wise_api_token
    },
    jsondecode(var.wise_profile_ids)
  )

  type = "Opaque"
}

# LLM API credentials (Anthropic + OpenAI)
resource "kubernetes_secret" "llm_credentials" {
  count = var.anthropic_api_key != "" || var.openai_api_key != "" ? 1 : 0

  metadata {
    name      = "${var.agent_name}-llm-credentials"
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    ANTHROPIC_API_KEY = var.anthropic_api_key
    OPENAI_API_KEY    = var.openai_api_key
  }

  type = "Opaque"
}

# Slack App credentials
resource "kubernetes_secret" "slack_credentials" {
  count = var.slack_bot_token != "" ? 1 : 0

  metadata {
    name      = "${var.agent_name}-slack-credentials"
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    SLACK_BOT_TOKEN      = var.slack_bot_token
    SLACK_SIGNING_SECRET = var.slack_signing_secret
  }

  type = "Opaque"
}

# Spectre API credentials
resource "kubernetes_secret" "spectre_credentials" {
  count = var.spectre_api_key != "" ? 1 : 0

  metadata {
    name      = "${var.agent_name}-spectre-credentials"
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    SPECTRE_API_URL = var.spectre_api_url
    SPECTRE_API_KEY = var.spectre_api_key
  }

  type = "Opaque"
}

resource "kubernetes_deployment" "app" {
  metadata {
    name      = var.agent_name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    replicas = var.replicas

    selector {
      match_labels = {
        "app.kubernetes.io/name" = var.agent_name
      }
    }

    template {
      metadata {
        labels = local.labels
      }

      spec {
        image_pull_secrets {
          name = "registry-pull-secret"
        }

        container {
          name  = var.agent_name
          image = "${local.registry_hostname}/${var.agent_name}:${var.image_tag}"

          port {
            container_port = var.container_port
            protocol       = "TCP"
          }

          env_from {
            secret_ref {
              name     = "postgresql-credentials"
              optional = true
            }
          }

          env_from {
            secret_ref {
              name     = "redis-credentials"
              optional = true
            }
          }

          env_from {
            secret_ref {
              name     = "qdrant-credentials"
              optional = true
            }
          }

          env_from {
            secret_ref {
              name     = "${var.agent_name}-wise-credentials"
              optional = true
            }
          }

          env_from {
            secret_ref {
              name     = "${var.agent_name}-llm-credentials"
              optional = true
            }
          }

          env_from {
            secret_ref {
              name     = "${var.agent_name}-slack-credentials"
              optional = true
            }
          }

          env_from {
            secret_ref {
              name     = "${var.agent_name}-spectre-credentials"
              optional = true
            }
          }

          # Mount Wise private key as file if secret exists
          dynamic "volume_mount" {
            for_each = var.wise_private_key != "" ? [1] : []
            content {
              name       = "wise-credentials"
              mount_path = "/secrets/wise"
              read_only  = true
            }
          }

          resources {
            requests = {
              cpu    = var.cpu_request
              memory = var.memory_request
            }
            limits = {
              cpu    = var.cpu_limit
              memory = var.memory_limit
            }
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = var.container_port
            }
            initial_delay_seconds = 10
            period_seconds        = 30
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/health/ready"
              port = var.container_port
            }
            initial_delay_seconds = 5
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          security_context {
            run_as_non_root            = true
            run_as_user                = 1000
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
          }
        }

        security_context {
          fs_group = 1000
        }

        # Volume for Wise private key
        dynamic "volume" {
          for_each = var.wise_private_key != "" ? [1] : []
          content {
            name = "wise-credentials"
            secret {
              secret_name = "${var.agent_name}-wise-credentials"
              items {
                key  = "wise_private.pem"
                path = "wise_private.pem"
              }
            }
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "app" {
  metadata {
    name      = var.agent_name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    selector = {
      "app.kubernetes.io/name" = var.agent_name
    }

    port {
      port        = var.service_port
      target_port = var.container_port
      protocol    = "TCP"
    }

    type = "ClusterIP"
  }
}

resource "kubernetes_ingress_v1" "app" {
  metadata {
    name      = var.agent_name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    ingress_class_name = "nginx"

    rule {
      host = "${var.agent_name}.${var.domain}"

      http {
        path {
          path      = "/"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.app.metadata[0].name
              port {
                number = var.service_port
              }
            }
          }
        }
      }
    }
  }
}

resource "kubernetes_network_policy" "app" {
  metadata {
    name      = "${var.agent_name}-allow-ingress"
    namespace = var.namespace
  }

  spec {
    pod_selector {
      match_labels = {
        "app.kubernetes.io/name" = var.agent_name
      }
    }

    ingress {
      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = var.namespace
          }
        }
      }

      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "ingress-nginx"
          }
        }
      }

      ports {
        port     = var.container_port
        protocol = "TCP"
      }
    }

    policy_types = ["Ingress"]
  }
}
