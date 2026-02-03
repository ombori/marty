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

# Wise API credentials (optional - only for agents that need Wise integration)
variable "wise_private_key" {
  description = "Wise SCA private key (base64 encoded)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "wise_api_token" {
  description = "Wise API token (single token for all profiles)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "wise_profile_ids" {
  description = "JSON object with Wise profile IDs"
  type        = string
  default     = "{}"
  sensitive   = true
}

# LLM API keys
variable "anthropic_api_key" {
  description = "Anthropic API key for LLM matching"
  type        = string
  default     = ""
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key for embeddings"
  type        = string
  default     = ""
  sensitive   = true
}

# Slack App credentials
variable "slack_bot_token" {
  description = "Slack bot token (xoxb-...)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "slack_signing_secret" {
  description = "Slack signing secret for request verification"
  type        = string
  default     = ""
  sensitive   = true
}

# Spectre API
variable "spectre_api_url" {
  description = "Spectre API base URL"
  type        = string
  default     = "http://spectre.agents.svc.cluster.local"
}

variable "spectre_api_key" {
  description = "Spectre API key"
  type        = string
  default     = ""
  sensitive   = true
}
