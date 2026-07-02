import { knowledgeService } from './request'

// 纯 RAG 问答：直接调知识库 /query 接口，不经过 Agent 平台
export function queryKnowledgeRag(question, kbScope) {
  return knowledgeService({
    url: '/query',
    method: 'post',
    data: {
      question,
      ...(kbScope ? { kb_scope: kbScope } : {})
    },
    timeout: 60000
  })
}
