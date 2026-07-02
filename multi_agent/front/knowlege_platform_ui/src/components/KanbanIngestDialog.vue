<template>
  <el-dialog v-model="visible" title="解析入库" width="480px" :close-on-click-modal="false">
    <div class="ingest-body">
      <p class="file-name">
        <el-icon><Document /></el-icon>
        {{ filename }}
      </p>
      <el-form label-position="top" style="margin-top:16px">
        <el-form-item label="知识库分区（可选）">
          <el-input v-model="partition" placeholder="留空则入默认分区" clearable />
        </el-form-item>
      </el-form>
    </div>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="loading" @click="confirm">确认入库</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Document } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: Boolean,
  filename: { type: String, default: '' },
  recordId: { type: Number, default: null },
  kanbanType: { type: String, default: 'rd' }, // 'rd' | 'cs'
})
const emit = defineEmits(['update:modelValue', 'done'])

const visible = ref(false)
const partition = ref('')
const loading = ref(false)

watch(() => props.modelValue, v => { visible.value = v })
watch(visible, v => emit('update:modelValue', v))

async function confirm() {
  if (!props.recordId || !props.filename) return
  loading.value = true
  try {
    const url = `/api/kanban/${props.kanbanType}/records/${props.recordId}/ingest`
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('kp_auth_token') || ''}`,
      },
      body: JSON.stringify({ filename: props.filename, partition: partition.value }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    ElMessage.success('已提交入库，处理中...')
    emit('done', { filename: props.filename, kb_status: 'loading' })
    visible.value = false
  } catch (e) {
    ElMessage.error(`入库失败：${e.message}`)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.ingest-body { padding: 0 4px; }
.file-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #334155;
  background: #f8fafc;
  border-radius: 6px;
  padding: 10px 12px;
  word-break: break-all;
}
</style>
