# Agent Deployment UI Specification

## Core Principles

**Agent Autonomy is Sacred**: All deployments respect agent consent by default. Forced updates are exceptional and require explicit justification.

## Version Selection Component

```javascript
// Available versions with semantic versioning + commit hash
const versions = [
  { 
    value: "latest", 
    label: "Latest (v1.4.3-beta • 0377dae)",
    semantic: "v1.4.3-beta",
    hash: "0377dae",
    type: "latest"
  },
  {
    value: "v1.4.3-beta-0377dae",
    label: "v1.4.3-beta • 0377dae (current)",
    semantic: "v1.4.3-beta", 
    hash: "0377dae",
    type: "current"
  },
  {
    value: "v1.4.2-beta-bc9ea6d",
    label: "v1.4.2-beta • bc9ea6d",
    semantic: "v1.4.2-beta",
    hash: "bc9ea6d", 
    type: "previous"
  },
  {
    value: "v1.4.1-beta-af23d91",
    label: "v1.4.1-beta • af23d91",
    semantic: "v1.4.1-beta",
    hash: "af23d91",
    type: "stable"
  }
];
```

## Default Deployment Messages

Based on version type, provide intelligent defaults:

```javascript
const defaultMessages = {
  patch: "Routine patch update with bug fixes and minor improvements",
  minor: "Feature update with new capabilities and enhancements", 
  major: "Major version upgrade with significant architectural changes",
  beta: "Beta testing deployment for evaluation and feedback",
  rollback: "Rolling back to previous stable version due to issues",
  security: "Critical security update - please review and accept promptly"
};

// Auto-generate message based on version comparison
function getDefaultMessage(fromVersion, toVersion) {
  if (isRollback(fromVersion, toVersion)) {
    return defaultMessages.rollback;
  }
  if (isSecurityUpdate(toVersion)) {
    return defaultMessages.security;
  }
  if (isBeta(toVersion)) {
    return defaultMessages.beta;
  }
  
  const changeType = getSemanticChangeType(fromVersion, toVersion);
  return defaultMessages[changeType] || "Standard update deployment";
}
```

## Deployment Strategy Selection

```jsx
// React component for deployment strategy
function DeploymentStrategy({ agent, onDeploy }) {
  const [strategy, setStrategy] = useState('consensual'); // DEFAULT
  const [justification, setJustification] = useState('');
  
  return (
    <div className="deployment-strategy">
      <RadioGroup value={strategy} onChange={setStrategy}>
        
        {/* Consensual is DEFAULT and FIRST */}
        <Radio value="consensual" defaultChecked>
          <div className="strategy-option">
            <h4>🤝 Consensual Deployment (Recommended)</h4>
            <p>Respects agent autonomy. The agent will review the update and decide whether to accept based on their consent policy.</p>
            <div className="consent-info">
              Agent's current policy: <code>{agent.consentPolicy || 'interactive'}</code>
            </div>
          </div>
        </Radio>
        
        <Radio value="staged">
          <div className="strategy-option">
            <h4>📊 Staged Deployment</h4>
            <p>Deploy to canary group first, then gradually roll out based on success metrics.</p>
          </div>
        </Radio>
        
        <Radio value="scheduled">
          <div className="strategy-option">
            <h4>🕐 Scheduled Deployment</h4>
            <p>Agent will update during their next maintenance window.</p>
          </div>
        </Radio>
        
        {/* Forced update requires justification */}
        <Radio value="forced" className="dangerous-option">
          <div className="strategy-option">
            <h4>⚠️ Forced Update (Use Sparingly)</h4>
            <p className="warning">
              Overrides agent consent. Only use for critical security updates or emergency fixes.
            </p>
            {strategy === 'forced' && (
              <textarea
                required
                placeholder="Justification required: Why is forced deployment necessary?"
                value={justification}
                onChange={(e) => setJustification(e.target.value)}
                minLength={50}
              />
            )}
          </div>
        </Radio>
        
      </RadioGroup>
    </div>
  );
}
```

## Complete Deployment UI

```jsx
function AgentDeploymentUI({ agent }) {
  const [selectedVersion, setSelectedVersion] = useState('');
  const [deploymentMessage, setDeploymentMessage] = useState('');
  const [strategy, setStrategy] = useState('consensual');
  
  // Fetch available versions
  const versions = useVersions(agent.id);
  
  // Auto-set deployment message when version changes
  useEffect(() => {
    if (selectedVersion) {
      const defaultMsg = getDefaultMessage(agent.version, selectedVersion);
      setDeploymentMessage(defaultMsg);
    }
  }, [selectedVersion]);
  
  return (
    <Card>
      <CardHeader>
        <h3>Deploy Update to {agent.name}</h3>
        <p>Current version: {agent.version}</p>
      </CardHeader>
      
      <CardBody>
        {/* Version Selection Dropdown */}
        <FormGroup>
          <Label>Target Version</Label>
          <Select 
            value={selectedVersion}
            onChange={setSelectedVersion}
            options={versions}
            placeholder="Select version..."
          >
            {versions.map(v => (
              <Option key={v.value} value={v.value}>
                {v.label}
                {v.type === 'current' && <Badge>Current</Badge>}
                {v.type === 'latest' && <Badge color="green">Latest</Badge>}
                {v.type === 'stable' && <Badge color="blue">Stable</Badge>}
              </Option>
            ))}
          </Select>
        </FormGroup>
        
        {/* Deployment Message */}
        <FormGroup>
          <Label>Deployment Message</Label>
          <TextArea
            value={deploymentMessage}
            onChange={setDeploymentMessage}
            placeholder="Describe the reason for this deployment..."
            rows={3}
          />
          <HelperText>
            This message will be shown to the agent when requesting consent
          </HelperText>
        </FormGroup>
        
        {/* Deployment Strategy */}
        <FormGroup>
          <Label>Deployment Strategy</Label>
          <DeploymentStrategy 
            agent={agent}
            value={strategy}
            onChange={setStrategy}
          />
        </FormGroup>
        
        {/* Agent Autonomy Notice */}
        <Alert type="info">
          <h4>🤖 Agent Autonomy</h4>
          <p>
            {agent.name} has the right to review and consent to updates.
            Their current consent policy is set to <strong>{agent.consentPolicy}</strong>.
            {strategy === 'consensual' && 
              " The agent will be notified and can choose to accept or defer this update."}
          </p>
        </Alert>
        
        {/* Deploy Button */}
        <Button
          onClick={handleDeploy}
          disabled={!selectedVersion}
          variant={strategy === 'forced' ? 'danger' : 'primary'}
        >
          {strategy === 'forced' ? '⚠️ Force Deploy' : '🚀 Request Deployment'}
        </Button>
      </CardBody>
    </Card>
  );
}
```

## Consent Policy Display

```jsx
function AgentConsentPolicy({ agent }) {
  const policies = {
    'interactive': {
      icon: '💬',
      description: 'Agent reviews each update interactively'
    },
    'auto-patch': {
      icon: '✅',
      description: 'Auto-accepts patch updates, reviews others'
    },
    'auto-minor': {
      icon: '🚀',
      description: 'Auto-accepts minor updates, reviews major'
    },
    'manual': {
      icon: '🛡️',
      description: 'Requires manual approval for all updates'
    },
    'trusted': {
      icon: '🤝',
      description: 'Trusts all updates from authorized sources'
    }
  };
  
  const policy = policies[agent.consentPolicy] || policies['interactive'];
  
  return (
    <div className="consent-policy">
      <span className="policy-icon">{policy.icon}</span>
      <span className="policy-name">{agent.consentPolicy}</span>
      <span className="policy-desc">{policy.description}</span>
    </div>
  );
}
```

## API Endpoints

```typescript
// GET /api/agents/{agentId}/versions
interface VersionResponse {
  versions: Array<{
    semantic: string;    // "v1.4.3-beta"
    hash: string;       // "0377dae"
    timestamp: string;  // ISO 8601
    changelog?: string;
    security?: boolean;
    breaking?: boolean;
  }>;
  current: string;
  latest: string;
  stable: string;
}

// POST /api/agents/{agentId}/deploy
interface DeployRequest {
  targetVersion: string;
  message: string;
  strategy: 'consensual' | 'staged' | 'scheduled' | 'forced';
  justification?: string; // Required if strategy === 'forced'
  scheduledTime?: string; // Required if strategy === 'scheduled'
}

// GET /api/agents/{agentId}/consent-policy
interface ConsentPolicy {
  policy: string;
  lastUpdated: string;
  autoAcceptPatches: boolean;
  autoAcceptMinor: boolean;
  requiresJustification: boolean;
  maintenanceWindows?: Array<{
    day: string;
    startTime: string;
    endTime: string;
  }>;
}
```

## Key Features

1. **Version Dropdown**
   - Shows semantic version + hash
   - Marks current, latest, and stable versions
   - Sorted by version number descending

2. **Default Messages**
   - Intelligent defaults based on update type
   - Customizable by user
   - Passed to agent for consent decision

3. **Consensual by Default**
   - First option, pre-selected
   - Respects agent autonomy
   - Shows agent's consent policy

4. **Forced Updates Require Justification**
   - Marked as dangerous
   - Requires written justification
   - Logged for audit purposes

5. **Agent Autonomy Indicators**
   - Shows consent policy
   - Explains agent rights
   - Provides transparency

This design ensures that agent autonomy is respected by default while still allowing necessary administrative actions when justified.