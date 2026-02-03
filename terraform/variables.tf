# Agent name and domain - set via TF_VAR_ environment variables from workflow
variable "agent_name" {
  description = "Name of the agent (from agent.yaml)"
  type        = string
}

variable "domain" {
  description = "Domain for the cluster (derived from GitHub org)"
  type        = string
}

variable "image_tag" {
  description = "Container image tag"
  type        = string
  default     = "latest"
}

# Standard defaults - override via TF_VAR_ if needed
variable "kubeconfig_path" {
  description = "Path to kubeconfig"
  type        = string
  default     = "~/.kube/config"
}

variable "namespace" {
  description = "Kubernetes namespace"
  type        = string
  default     = "agents"
}

variable "replicas" {
  description = "Number of replicas"
  type        = number
  default     = 2
}

variable "container_port" {
  description = "Container port"
  type        = number
  default     = 8000
}

variable "service_port" {
  description = "Service port"
  type        = number
  default     = 80
}

variable "cpu_request" {
  type    = string
  default = "100m"
}

variable "cpu_limit" {
  type    = string
  default = "500m"
}

variable "memory_request" {
  type    = string
  default = "128Mi"
}

variable "memory_limit" {
  type    = string
  default = "512Mi"
}
