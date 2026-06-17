import axios from 'axios'

const attachUserHeader = (config) => {
  const userId = localStorage.getItem('kp_user_id') || localStorage.getItem('kp_user') || ''
  const authToken = localStorage.getItem('kp_auth_token') || ''
  if (userId) {
    config.headers = config.headers || {}
    config.headers['X-User-Id'] = userId
  }
  if (authToken) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${authToken}`
  }
  return config
}

const service = axios.create({
  baseURL: '/api',
  timeout: 100000
})

const knowledgeService = axios.create({
  baseURL: '/knowledge-api',
  timeout: 100000
})

const handleResponse = response => {
  return response.data
}

const handleError = error => {
  console.error('Request Error:', error)
  const detail = error.response?.data?.detail
  error.message = detail || error.message
  return Promise.reject(error)
}

service.interceptors.request.use(attachUserHeader)
knowledgeService.interceptors.request.use(attachUserHeader)
service.interceptors.response.use(handleResponse, handleError)
knowledgeService.interceptors.response.use(
  handleResponse,
  handleError
)

export default service
export { knowledgeService }
