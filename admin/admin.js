/**
 * CIRISLens Admin Interface
 * Manages telemetry collection and public visibility for CIRIS agents
 */

// Configuration
const MANAGER_API_URL = 'https://agents.ciris.ai/manager/v1';
const LENS_API_URL = '/api/admin'; // Local API endpoint for CIRISLens admin
const OAUTH_CLIENT_ID = 'YOUR_GOOGLE_CLIENT_ID'; // Set via environment
const ALLOWED_DOMAIN = 'ciris.ai';

// State
let currentUser = null;
let agents = [];
let managers = [];
let telemetryConfigs = {};
let visibilityConfigs = {};
let selectedManagerId = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    await checkAuthentication();
});

/**
 * Check if user is authenticated with OAuth
 */
async function checkAuthentication() {
    try {
        // Check for existing session
        const response = await fetch(`${LENS_API_URL}/auth/status`, {
            credentials: 'include'
        });

        if (response.ok) {
            const data = await response.json();
            if (data.authenticated && data.user) {
                currentUser = data.user;
                // Verify domain
                if (!currentUser.email.endsWith(`@${ALLOWED_DOMAIN}`)) {
                    showError('Access denied. Only @ciris.ai accounts are allowed.');
                    setTimeout(() => {
                        window.location.href = '/';
                    }, 3000);
                    return;
                }
                await initializeApp();
            } else {
                redirectToOAuth();
            }
        } else {
            redirectToOAuth();
        }
    } catch (error) {
        console.error('Auth check failed:', error);
        redirectToOAuth();
    }
}

/**
 * Redirect to OAuth login
 */
function redirectToOAuth() {
    const params = new URLSearchParams({
        client_id: OAUTH_CLIENT_ID,
        redirect_uri: `${window.location.origin}/admin/oauth/callback`,
        response_type: 'code',
        scope: 'openid email profile',
        hd: ALLOWED_DOMAIN, // Restrict to ciris.ai domain
        prompt: 'select_account'
    });
    
    window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params}`;
}

/**
 * Initialize the admin interface
 */
async function initializeApp() {
    // Show app
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
    
    // Set user info
    if (currentUser) {
        document.getElementById('user-email').textContent = currentUser.email;
        if (currentUser.picture) {
            const avatar = document.getElementById('user-avatar');
            avatar.src = currentUser.picture;
            avatar.classList.remove('hidden');
        }
    }
    
    // Load data
    await loadManagers();
    await refreshAgents();
}

/**
 * Load all managers
 */
async function loadManagers() {
    try {
        const response = await fetch(`${LENS_API_URL}/managers`, {
            credentials: 'include'
        });
        
        if (response.ok) {
            const data = await response.json();
            managers = data.managers || [];
            renderManagersList();
        } else {
            console.error('Failed to load managers');
        }
    } catch (error) {
        console.error('Error loading managers:', error);
    }
}

/**
 * Render managers list
 */
function renderManagersList() {
    const container = document.getElementById('managers-list');
    if (!container) return;
    
    container.innerHTML = '';
    
    managers.forEach(manager => {
        const item = document.createElement('div');
        item.className = 'manager-item';
        item.innerHTML = `
            <div class="manager-info">
                <h4>${manager.name}</h4>
                <p>${manager.url}</p>
                <p class="status">
                    ${manager.enabled ? '✅ Active' : '⏸️ Disabled'}
                    ${manager.last_error ? `<span class="error">⚠️ ${manager.last_error}</span>` : ''}
                </p>
            </div>
            <div class="manager-actions">
                <button onclick="editManager(${manager.id})">Edit</button>
                <button onclick="toggleManager(${manager.id}, ${!manager.enabled})">
                    ${manager.enabled ? 'Disable' : 'Enable'}
                </button>
                <button onclick="deleteManager(${manager.id})" class="danger">Delete</button>
            </div>
        `;
        container.appendChild(item);
    });
    
    // Add "Add Manager" button
    const addButton = document.createElement('button');
    addButton.className = 'add-manager-btn';
    addButton.textContent = '+ Add Manager';
    addButton.onclick = showAddManagerModal;
    container.appendChild(addButton);
}

/**
 * Show modal to add a new manager
 */
function showAddManagerModal() {
    const modal = document.getElementById('manager-modal');
    if (!modal) {
        createManagerModal();
    }
    
    document.getElementById('manager-modal').classList.remove('hidden');
    document.getElementById('manager-form').reset();
    document.getElementById('modal-title').textContent = 'Add Manager';
    selectedManagerId = null;
}

/**
 * Edit existing manager
 */
function editManager(managerId) {
    const manager = managers.find(m => m.id === managerId);
    if (!manager) return;
    
    const modal = document.getElementById('manager-modal');
    if (!modal) {
        createManagerModal();
    }
    
    document.getElementById('manager-modal').classList.remove('hidden');
    document.getElementById('modal-title').textContent = 'Edit Manager';
    document.getElementById('manager-name').value = manager.name;
    document.getElementById('manager-url').value = manager.url;
    document.getElementById('manager-description').value = manager.description || '';
    document.getElementById('manager-interval').value = manager.collection_interval_seconds || 30;
    selectedManagerId = managerId;
}

/**
 * Toggle manager enabled state
 */
async function toggleManager(managerId, enable) {
    try {
        const response = await fetch(`${LENS_API_URL}/managers/${managerId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({ enabled: enable })
        });
        
        if (response.ok) {
            showSuccess(`Manager ${enable ? 'enabled' : 'disabled'}`);
            await loadManagers();
        } else {
            showError('Failed to update manager');
        }
    } catch (error) {
        console.error('Error toggling manager:', error);
        showError('Failed to update manager');
    }
}

/**
 * Delete a manager
 */
async function deleteManager(managerId) {
    if (!confirm('Are you sure you want to delete this manager?')) {
        return;
    }
    
    try {
        const response = await fetch(`${LENS_API_URL}/managers/${managerId}`, {
            method: 'DELETE',
            credentials: 'include'
        });
        
        if (response.ok) {
            showSuccess('Manager deleted');
            await loadManagers();
        } else {
            showError('Failed to delete manager');
        }
    } catch (error) {
        console.error('Error deleting manager:', error);
        showError('Failed to delete manager');
    }
}

/**
 * Save manager (add or update)
 */
async function saveManager(event) {
    event.preventDefault();
    
    const formData = {
        name: document.getElementById('manager-name').value,
        url: document.getElementById('manager-url').value,
        description: document.getElementById('manager-description').value,
        collection_interval_seconds: parseInt(document.getElementById('manager-interval').value) || 30,
        enabled: true
    };
    
    const authToken = document.getElementById('manager-auth-token').value;
    if (authToken) {
        formData.auth_token = authToken;
    }
    
    try {
        const url = selectedManagerId 
            ? `${LENS_API_URL}/managers/${selectedManagerId}`
            : `${LENS_API_URL}/managers`;
        
        const method = selectedManagerId ? 'PUT' : 'POST';
        
        const response = await fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify(formData)
        });
        
        if (response.ok) {
            showSuccess(selectedManagerId ? 'Manager updated' : 'Manager added');
            document.getElementById('manager-modal').classList.add('hidden');
            await loadManagers();
            await refreshAgents();
        } else {
            const error = await response.json();
            showError(error.detail || 'Failed to save manager');
        }
    } catch (error) {
        console.error('Error saving manager:', error);
        showError('Failed to save manager');
    }
}

/**
 * Create manager modal HTML
 */
function createManagerModal() {
    const modal = document.createElement('div');
    modal.id = 'manager-modal';
    modal.className = 'modal hidden';
    modal.innerHTML = `
        <div class="modal-content">
            <div class="modal-header">
                <h2 id="modal-title">Add Manager</h2>
                <button onclick="document.getElementById('manager-modal').classList.add('hidden')" class="close">×</button>
            </div>
            <form id="manager-form" onsubmit="saveManager(event)">
                <div class="form-group">
                    <label for="manager-name">Name:</label>
                    <input type="text" id="manager-name" required>
                </div>
                <div class="form-group">
                    <label for="manager-url">URL:</label>
                    <input type="url" id="manager-url" required placeholder="https://agents.ciris.ai">
                </div>
                <div class="form-group">
                    <label for="manager-description">Description:</label>
                    <textarea id="manager-description" rows="3"></textarea>
                </div>
                <div class="form-group">
                    <label for="manager-interval">Collection Interval (seconds):</label>
                    <input type="number" id="manager-interval" min="10" max="3600" value="30">
                </div>
                <div class="form-group">
                    <label for="manager-auth-token">Auth Token (optional):</label>
                    <input type="password" id="manager-auth-token" placeholder="Bearer token if required">
                </div>
                <div class="modal-footer">
                    <button type="button" onclick="document.getElementById('manager-modal').classList.add('hidden')">Cancel</button>
                    <button type="submit" class="primary">Save</button>
                </div>
            </form>
        </div>
    `;
    document.body.appendChild(modal);
}

/**
 * Switch between tabs
 */
function switchTab(tabName) {
    // Hide all tab contents
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.add('hidden');
    });
    
    // Remove active class from all tab buttons
    document.querySelectorAll('.tab-button').forEach(button => {
        button.classList.remove('border-blue-500', 'text-blue-600');
        button.classList.add('border-transparent', 'text-gray-600');
    });
    
    // Show selected tab content
    document.getElementById(`${tabName}-content`).classList.remove('hidden');
    
    // Activate selected tab button
    const activeTab = document.getElementById(`${tabName}-tab`);
    activeTab.classList.remove('border-transparent', 'text-gray-600');
    activeTab.classList.add('border-blue-500', 'text-blue-600');
}

/**
 * Show success message
 */
function showSuccess(message) {
    const toast = document.createElement('div');
    toast.className = 'fixed top-4 right-4 bg-green-500 text-white px-6 py-3 rounded-lg shadow-lg z-50';
    toast.innerHTML = `<i class="fas fa-check-circle mr-2"></i>${message}`;
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.remove();
    }, 3000);
}

/**
 * Show error message
 */
function showError(message) {
    const toast = document.createElement('div');
    toast.className = 'fixed top-4 right-4 bg-red-500 text-white px-6 py-3 rounded-lg shadow-lg z-50';
    toast.innerHTML = `<i class="fas fa-exclamation-circle mr-2"></i>${message}`;
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.remove();
    }, 5000);
}

/**
 * Refresh agents from all managers
 */
async function refreshAgents() {
    try {
        // First, discover managers (for now, we have one)
        managers = [{
            manager_id: 'primary',
            name: 'Primary Manager',
            url: MANAGER_API_URL,
            status: 'online',
            last_seen: new Date().toISOString(),
            agent_count: 0
        }];
        
        // Fetch agents from manager API (no auth needed)
        const response = await fetch(`${MANAGER_API_URL}/agents`);
        if (!response.ok) throw new Error('Failed to fetch agents');
        
        const data = await response.json();
        agents = data.agents || [];
        
        // Fetch telemetry and visibility configs from our backend
        await loadConfigurations();
        
        // Update UI
        updateStats();
        renderAgentsTable();
        updateManagerFilter();
        
    } catch (error) {
        console.error('Failed to refresh agents:', error);
        showError('Failed to load agents. Please try again.');
    }
}

/**
 * Load telemetry and visibility configurations
 */
async function loadConfigurations() {
    try {
        const response = await fetch(`${LENS_API_URL}/configurations`, {
            credentials: 'include'
        });
        
        if (response.ok) {
            const data = await response.json();
            telemetryConfigs = data.telemetry || {};
            visibilityConfigs = data.visibility || {};
        }
    } catch (error) {
        console.error('Failed to load configurations:', error);
    }
}

/**
 * Update statistics
 */
function updateStats() {
    const totalAgents = agents.length;
    const telemetryActive = agents.filter(a => 
        telemetryConfigs[a.agent_id]?.enabled
    ).length;
    const publicVisible = agents.filter(a => 
        visibilityConfigs[a.agent_id]?.public_visible
    ).length;
    
    document.getElementById('total-agents').textContent = totalAgents;
    document.getElementById('telemetry-active').textContent = telemetryActive;
    document.getElementById('public-visible').textContent = publicVisible;
    document.getElementById('total-managers').textContent = managers.length;
}

/**
 * Render agents table
 */
function renderAgentsTable() {
    const tbody = document.getElementById('agents-table');
    tbody.innerHTML = '';
    
    agents.forEach(agent => {
        const telemetry = telemetryConfigs[agent.agent_id] || { enabled: false };
        const visibility = visibilityConfigs[agent.agent_id] || { public_visible: false };
        
        const row = document.createElement('tr');
        row.innerHTML = `
            <td class="px-6 py-4 whitespace-nowrap">
                <div>
                    <div class="text-sm font-medium text-gray-900">${agent.name}</div>
                    <div class="text-sm text-gray-500">${agent.agent_id}</div>
                    <div class="text-xs text-gray-400">${agent.version} - ${agent.codename}</div>
                </div>
            </td>
            <td class="px-6 py-4 whitespace-nowrap">
                <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full 
                    ${agent.status === 'running' ? 'bg-green-100 text-green-800' : 
                      agent.status === 'stopped' ? 'bg-gray-100 text-gray-800' : 
                      'bg-red-100 text-red-800'}">
                    ${agent.status}
                </span>
                <div class="text-xs text-gray-500 mt-1">${agent.cognitive_state}</div>
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                Primary Manager
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-center">
                <button onclick="toggleTelemetry('${agent.agent_id}')" 
                        class="relative inline-flex h-6 w-11 items-center rounded-full 
                        ${telemetry.enabled ? 'bg-green-600' : 'bg-gray-200'}">
                    <span class="inline-block h-4 w-4 transform rounded-full bg-white transition 
                           ${telemetry.enabled ? 'translate-x-6' : 'translate-x-1'}"></span>
                </button>
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-center">
                <button onclick="toggleVisibility('${agent.agent_id}')" 
                        class="relative inline-flex h-6 w-11 items-center rounded-full 
                        ${visibility.public_visible ? 'bg-purple-600' : 'bg-gray-200'}">
                    <span class="inline-block h-4 w-4 transform rounded-full bg-white transition 
                           ${visibility.public_visible ? 'translate-x-6' : 'translate-x-1'}"></span>
                </button>
            </td>
            <td class="px-6 py-4 whitespace-nowrap text-center">
                <button onclick="openSettings('${agent.agent_id}')" 
                        class="text-blue-600 hover:text-blue-900">
                    <i class="fas fa-cog"></i> Configure
                </button>
            </td>
        `;
        tbody.appendChild(row);
    });
}

/**
 * Toggle telemetry for an agent
 */
async function toggleTelemetry(agentId) {
    const current = telemetryConfigs[agentId]?.enabled || false;
    
    try {
        const response = await fetch(`${LENS_API_URL}/telemetry/${agentId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ enabled: !current })
        });
        
        if (response.ok) {
            telemetryConfigs[agentId] = {
                ...telemetryConfigs[agentId],
                enabled: !current,
                last_updated: new Date().toISOString(),
                updated_by: currentUser.email
            };
            renderAgentsTable();
            updateStats();
        } else {
            throw new Error('Failed to update telemetry');
        }
    } catch (error) {
        console.error('Failed to toggle telemetry:', error);
        showError('Failed to update telemetry settings');
    }
}

/**
 * Toggle visibility for an agent
 */
async function toggleVisibility(agentId) {
    const current = visibilityConfigs[agentId]?.public_visible || false;
    
    try {
        const response = await fetch(`${LENS_API_URL}/visibility/${agentId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ public_visible: !current })
        });
        
        if (response.ok) {
            visibilityConfigs[agentId] = {
                ...visibilityConfigs[agentId],
                public_visible: !current,
                last_updated: new Date().toISOString(),
                updated_by: currentUser.email
            };
            renderAgentsTable();
            updateStats();
        } else {
            throw new Error('Failed to update visibility');
        }
    } catch (error) {
        console.error('Failed to toggle visibility:', error);
        showError('Failed to update visibility settings');
    }
}

/**
 * Open settings modal for an agent
 */
function openSettings(agentId) {
    const agent = agents.find(a => a.agent_id === agentId);
    const telemetry = telemetryConfigs[agentId] || {};
    const visibility = visibilityConfigs[agentId] || {};
    
    // Set modal title
    document.getElementById('modal-agent-id').textContent = agent.name;
    
    // Set telemetry settings
    document.getElementById('telemetry-enabled').checked = telemetry.enabled || false;
    document.getElementById('metrics-enabled').checked = telemetry.metrics_enabled !== false;
    document.getElementById('traces-enabled').checked = telemetry.traces_enabled !== false;
    document.getElementById('logs-enabled').checked = telemetry.logs_enabled !== false;
    document.getElementById('collection-interval').value = telemetry.collection_interval || 60;
    
    // Set visibility settings
    document.getElementById('public-visible').checked = visibility.public_visible || false;
    document.getElementById('show-metrics').checked = visibility.show_metrics !== false;
    document.getElementById('show-traces').checked = visibility.show_traces !== false;
    document.getElementById('show-logs').checked = visibility.show_logs !== false;
    document.getElementById('show-cognitive-state').checked = visibility.show_cognitive_state !== false;
    document.getElementById('show-health-status').checked = visibility.show_health_status !== false;
    document.getElementById('redact-pii').checked = true; // Always on
    
    // Store agent ID for save
    document.getElementById('settings-modal').dataset.agentId = agentId;
    
    // Show modal
    document.getElementById('settings-modal').classList.remove('hidden');
}

/**
 * Close settings modal
 */
function closeSettingsModal() {
    document.getElementById('settings-modal').classList.add('hidden');
}

/**
 * Save settings from modal
 */
async function saveSettings() {
    const agentId = document.getElementById('settings-modal').dataset.agentId;
    
    const telemetryUpdate = {
        agent_id: agentId,
        enabled: document.getElementById('telemetry-enabled').checked,
        metrics_enabled: document.getElementById('metrics-enabled').checked,
        traces_enabled: document.getElementById('traces-enabled').checked,
        logs_enabled: document.getElementById('logs-enabled').checked,
        collection_interval: parseInt(document.getElementById('collection-interval').value)
    };
    
    const visibilityUpdate = {
        agent_id: agentId,
        public_visible: document.getElementById('public-visible').checked,
        show_metrics: document.getElementById('show-metrics').checked,
        show_traces: document.getElementById('show-traces').checked,
        show_logs: document.getElementById('show-logs').checked,
        show_cognitive_state: document.getElementById('show-cognitive-state').checked,
        show_health_status: document.getElementById('show-health-status').checked,
        redact_pii: true // Always true
    };
    
    try {
        // Update telemetry
        await fetch(`${LENS_API_URL}/telemetry/${agentId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(telemetryUpdate)
        });
        
        // Update visibility
        await fetch(`${LENS_API_URL}/visibility/${agentId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(visibilityUpdate)
        });
        
        // Update local state
        telemetryConfigs[agentId] = {
            ...telemetryUpdate,
            last_updated: new Date().toISOString(),
            updated_by: currentUser.email
        };
        visibilityConfigs[agentId] = {
            ...visibilityUpdate,
            last_updated: new Date().toISOString(),
            updated_by: currentUser.email
        };
        
        closeSettingsModal();
        renderAgentsTable();
        updateStats();
        showSuccess('Settings saved successfully');
        
    } catch (error) {
        console.error('Failed to save settings:', error);
        showError('Failed to save settings');
    }
}

/**
 * Toggle telemetry for all agents
 */
async function toggleAllTelemetry(enable) {
    const updates = agents.map(agent => 
        fetch(`${LENS_API_URL}/telemetry/${agent.agent_id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ enabled: enable })
        })
    );
    
    try {
        await Promise.all(updates);
        await loadConfigurations();
        renderAgentsTable();
        updateStats();
        showSuccess(`Telemetry ${enable ? 'enabled' : 'disabled'} for all agents`);
    } catch (error) {
        console.error('Failed to toggle all telemetry:', error);
        showError('Failed to update some agents');
    }
}

/**
 * Update manager filter dropdown
 */
function updateManagerFilter() {
    const select = document.getElementById('filter-manager');
    select.innerHTML = '<option value="">All Managers</option>';
    
    managers.forEach(manager => {
        const option = document.createElement('option');
        option.value = manager.manager_id;
        option.textContent = manager.name;
        select.appendChild(option);
    });
}

/**
 * Apply filters to the table
 */
function applyFilters() {
    const managerFilter = document.getElementById('filter-manager').value;
    const statusFilter = document.getElementById('filter-status').value;
    
    // Filter logic would go here
    // For now, just re-render
    renderAgentsTable();
}

/**
 * Logout
 */
async function logout() {
    try {
        await fetch(`${LENS_API_URL}/auth/logout`, {
            method: 'POST',
            credentials: 'include'
        });
    } catch (error) {
        console.error('Logout error:', error);
    }
    
    window.location.href = '/';
}

/**
 * Show error message
 */
function showError(message) {
    // In production, use a proper toast notification
    console.error(message);
    alert(message);
}

/**
 * Show success message
 */
function showSuccess(message) {
    // In production, use a proper toast notification
    console.log(message);
}