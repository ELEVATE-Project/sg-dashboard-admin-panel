pipeline {
    agent any
    parameters {
        choice(name: 'GIT_REF_TYPE', choices: ['Branch', 'Tag'], description: 'Select whether to clone a Branch or a Tag')
        string(name: 'GIT_REF', defaultValue: 'main', description: 'Enter the branch name or tag to clone')
    }
    options {
        disableConcurrentBuilds()
    }
    stages {
        stage('Checkout') {
            steps {
                script {
                    if (params.GIT_REF == "") {
                        error "Invalid Branch or Tag. Please enter 'Branch' or 'Tag' "
                    } else if (params.GIT_REF_TYPE == 'Branch') {
                        checkout([$class: 'GitSCM', 
                                  branches: [[name: "refs/heads/${params.GIT_REF}"]],
                                  doGenerateSubmoduleConfigurations: false,
                                  extensions: [],
                                  submoduleCfg: [],
                                  userRemoteConfigs: [[url: 'https://github.com/ELEVATE-Project/sg-dashboard-admin-panel']]])
                    } else if (params.GIT_REF_TYPE == 'Tag') {
                        checkout([$class: 'GitSCM', 
                                  branches: [[name: "refs/tags/${params.GIT_REF}"]],
                                  doGenerateSubmoduleConfigurations: false,
                                  extensions: [],
                                  submoduleCfg: [],
                                  userRemoteConfigs: [[url: 'https://github.com/ELEVATE-Project/sg-dashboard-admin-panel']]])
                    } else {
                        error "Invalid GIT_REF_TYPE. Please choose 'Branch' or 'Tag'."
                    }
                }
            }
        }
        
        stage("Ansible Run") {
            steps {
                withCredentials([usernamePassword(credentialsId: 'git-credential', passwordVariable: 'GIT_TOKEN', usernameVariable: 'GIT_EMAIL')]) {
                    // Run Ansible playbook
                    ansiblePlaybook becomeUser: 'root', 
                    credentialsId: 'elevate', 
                    extras: "-e gitBranch=${GIT_REF} -e GIT_USER_NAME=tech-infra -e GIT_EMAIL=${GIT_EMAIL} -e GIT_TOKEN=${GIT_TOKEN}", 
                    installation: 'ansible', 
                    inventory: '/etc/ansible/postgres-hosts', 
                    playbook: 'deployment/ansible.yml'
                }
            }
        }
    }
}
