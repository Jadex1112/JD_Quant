# Chapter 13 – User Classes and Personas

## 13.1 Purpose

This chapter defines the user classes, personas, responsibilities, access boundaries, operational objectives, and interaction patterns for all categories of users who interact with the JD Quant AI platform.

The purpose of this chapter is to establish a common understanding of platform users before defining detailed functional requirements. These user classes form the basis for:

- Role-Based Access Control (RBAC)
- Authentication and authorization
- User interface design
- Workflow design
- Notification routing
- Audit logging
- Security policies
- Approval workflows
- Acceptance testing

Every user shall be assigned one or more roles. Permissions shall be granted to roles rather than directly to individual user accounts wherever practical.

## 13.2 User Classification Model

JD Quant AI classifies users according to their operational responsibilities.

The primary user classes are:

| User Class | Primary Responsibility |
|---|---|
| System Administrator | Platform administration and governance |
| Quantitative Researcher | Research and model development |
| Quantitative Trader | Live and simulated trading |
| Portfolio Manager | Portfolio supervision and allocation |
| Risk Manager | Risk governance and limit enforcement |
| AI/ML Engineer | AI model lifecycle management |
| Data Engineer | Data ingestion and processing |
| Operations Engineer | Operational monitoring and maintenance |
| Security Administrator | Security management |
| Compliance Officer | Governance and audit review |
| Executive / Viewer | Read-only monitoring and reporting |
| Auditor | Independent review and traceability |
| API Client | Programmatic system access |
| Service Account | Machine-to-machine operations |

Additional user classes may be introduced through future platform extensions.

## 13.3 User Lifecycle

Every user account shall follow a defined lifecycle.

Typical lifecycle stages include:

- Account creation
- Identity verification
- Role assignment
- Permission provisioning
- Initial authentication
- Active usage
- Permission modification
- Temporary suspension (if applicable)
- Reactivation
- Deactivation
- Archival
- Permanent deletion (where permitted)

Each lifecycle event shall be recorded within the audit system.

## 13.4 System Administrator

### Description

The System Administrator is responsible for overall platform configuration, governance, infrastructure coordination, and administrative oversight.

### Responsibilities

- User administration
- Role management
- Platform configuration
- Exchange configuration
- Broker configuration
- Feature management
- Backup management
- Operational governance
- Environment configuration
- System maintenance

### Typical Activities

- Create user accounts
- Configure platform settings
- Review system health
- Manage integrations
- Initiate maintenance windows
- Review operational alerts
- Configure notifications

### Access Level

Highest administrative privileges subject to organizational security policies.

## 13.5 Quantitative Researcher

### Description

The Quantitative Researcher develops and validates trading hypotheses using historical data and analytical methods.

### Responsibilities

- Research datasets
- Build indicators
- Engineer features
- Evaluate signals
- Develop strategies
- Validate statistical assumptions
- Document experiments

### Typical Activities

- Explore market data
- Compare strategies
- Perform optimization
- Analyze research metrics
- Export research findings

### Access Level

Research-related resources only unless additional permissions are granted.

## 13.6 Quantitative Trader

### Description

The Quantitative Trader supervises strategy execution and manages trading operations.

### Responsibilities

- Monitor strategies
- Review executions
- Manage positions
- Approve deployments (where authorized)
- Supervise live trading
- Respond to operational alerts

### Typical Activities

- Launch approved strategies
- Pause trading
- Review order status
- Analyze performance
- Review execution quality

### Access Level

Trading functionality according to assigned permissions.

## 13.7 Portfolio Manager

### Description

The Portfolio Manager supervises investment portfolios.

### Responsibilities

- Portfolio construction
- Asset allocation
- Exposure monitoring
- Benchmark comparison
- Performance evaluation
- Capital allocation

### Typical Activities

- Review allocation
- Compare benchmarks
- Monitor diversification
- Analyze returns
- Review portfolio reports

### Access Level

Portfolio management resources only.

## 13.8 Risk Manager

### Description

The Risk Manager ensures trading activity remains within organizational risk policies.

### Responsibilities

- Configure limits
- Review exposures
- Monitor drawdowns
- Approve risk exceptions
- Investigate violations
- Authorize emergency actions

### Typical Activities

- Configure limits
- Review dashboards
- Suspend strategies
- Generate risk reports
- Audit trading activity

### Access Level

Risk management resources with authority defined by organizational policy.

## 13.9 AI/ML Engineer

### Description

The AI/ML Engineer manages artificial intelligence models throughout their lifecycle.

### Responsibilities

- Build models
- Train models
- Validate models
- Deploy models
- Monitor performance
- Detect drift
- Retrain models

### Typical Activities

- Launch training jobs
- Review metrics
- Compare model versions
- Deploy approved models
- Monitor inference

### Access Level

AI infrastructure and model management resources.

## 13.10 Data Engineer

### Description

The Data Engineer manages data acquisition, transformation, quality, and storage.

### Responsibilities

- Data ingestion
- ETL pipelines
- Data validation
- Historical imports
- Schema evolution
- Metadata management

### Typical Activities

- Configure providers
- Monitor ingestion
- Resolve data quality issues
- Validate datasets
- Schedule imports

### Access Level

Data management resources.

## 13.11 Operations Engineer

### Description

The Operations Engineer maintains production platform stability.

### Responsibilities

- Monitor health
- Respond to incidents
- Schedule maintenance
- Manage deployments
- Verify backups
- Review operational metrics

### Typical Activities

- Review dashboards
- Restart services
- Investigate failures
- Execute recovery procedures
- Validate deployments

### Access Level

Operational infrastructure resources.

## 13.12 Security Administrator

### Description

The Security Administrator manages cybersecurity controls across the platform.

### Responsibilities

- Authentication policies
- Authorization policies
- Secret management
- Key rotation
- Security monitoring
- Incident response
- Vulnerability review

### Typical Activities

- Review login activity
- Rotate credentials
- Configure access policies
- Investigate security alerts
- Audit permissions

### Access Level

Security administration resources.

## 13.13 Compliance Officer

### Description

The Compliance Officer reviews platform activities for adherence to organizational and regulatory policies.

### Responsibilities

- Review audit logs
- Validate reporting
- Verify record retention
- Assess operational controls
- Review approvals

### Typical Activities

- Generate compliance reports
- Review configuration history
- Audit access records
- Verify approval workflows

### Access Level

Read-only access to governance resources unless otherwise authorized.

## 13.14 Executive / Viewer

### Description

Executive users require visibility into operational and business performance without performing system administration or trading activities.

### Responsibilities

- Review KPIs
- Monitor business performance
- Evaluate portfolio summaries
- Review operational dashboards

### Typical Activities

- Open dashboards
- Export reports
- Review performance trends
- Monitor strategic metrics

### Access Level

Read-only.

## 13.15 Auditor

### Description

Auditors independently review platform activity for governance, traceability, and operational integrity.

### Responsibilities

- Audit history
- Configuration review
- Change verification
- User activity review
- Security review

### Typical Activities

- Search audit logs
- Review approvals
- Verify traceability
- Generate audit reports

### Access Level

Read-only audit access.

## 13.16 API Client

### Description

API Clients represent external software integrating with JD Quant AI.

### Responsibilities

- Submit requests
- Consume APIs
- Exchange structured data
- Receive notifications

### Authentication

API clients shall authenticate using approved mechanisms such as API keys, OAuth tokens, mutual TLS, or equivalent methods defined in the security architecture.

### Access Level

Restricted to explicitly granted API permissions.

## 13.17 Service Account

### Description

Service Accounts are non-human identities used for automated platform operations.

Examples include:

- Scheduled jobs
- AI training pipelines
- ETL pipelines
- Monitoring services
- Backup services
- Deployment automation

Service Accounts shall:

- Use non-interactive authentication
- Follow least-privilege principles
- Be uniquely identifiable
- Support credential rotation
- Generate audit records

## 13.18 Permission Model

Permissions shall be assigned through roles rather than directly to individual users.

Permission categories include:

- View
- Create
- Modify
- Delete
- Execute
- Approve
- Configure
- Deploy
- Export
- Audit
- Administer

Fine-grained permission definitions will be specified in the Security Requirements chapter.

## 13.19 Authentication Requirements

All users shall authenticate before accessing protected platform resources.

Supported authentication mechanisms may include:

- Username and password
- Multi-factor authentication (MFA)
- Enterprise Single Sign-On (SSO)
- OAuth/OpenID Connect
- Certificate-based authentication
- API authentication
- Service account credentials

Authentication mechanisms shall comply with the platform’s security policies.

## 13.20 Authorization Principles

Authorization shall follow the principles of:

- Least privilege
- Separation of duties
- Role inheritance (where appropriate)
- Explicit permission assignment
- Default deny
- Time-bound elevation (where supported)
- Complete auditability

Authorization decisions shall be enforced consistently across all interfaces and APIs.

## 13.21 User Interaction Characteristics

All user classes shall interact with the platform through one or more supported interfaces, including:

- Desktop application
- Web interface
- REST APIs
- Internal APIs
- Administrative dashboards
- Reporting dashboards
- Notification channels

Future interfaces may include mobile applications and command-line tools without changing the underlying permission model.

## 13.22 User Experience Considerations

The user experience shall adapt to each user class by:

- Displaying relevant dashboards
- Presenting role-appropriate navigation
- Restricting inaccessible functions
- Prioritizing frequently used workflows
- Providing contextual guidance
- Supporting configurable workspaces

The interface shall minimize unnecessary complexity while exposing advanced functionality to authorized users.

## 13.23 User Governance

User accounts shall be subject to governance policies, including:

- Identity verification
- Role approval
- Periodic access review
- Credential management
- Session management
- Account suspension
- Account deactivation
- Audit logging
- Access recertification

Governance processes shall support organizational security and compliance objectives.

## 13.24 Chapter Summary

This chapter defines the user ecosystem of JD Quant AI by identifying all major user classes, their responsibilities, operational goals, access privileges, authentication methods, authorization principles, and interaction patterns. These definitions establish the foundation for Role-Based Access Control (RBAC), workflow authorization, UI personalization, audit logging, and security requirements throughout the platform.

Subsequent chapters will build upon these user definitions when specifying detailed functional behavior, permission models, workflow approvals, and interface requirements.

---

*End of Chapter 13 – User Classes and Personas*
