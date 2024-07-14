### 运行容器
运行容器，可以通过环境变量传递配置信息，例如代理URL和Token。以下是一个示例：

```yaml
version: '3'
services:
  gcp-claude:
    container_name: gcp-claude
    image: coulsontl/gcp-claude:latest
    restart: always
    network_mode: bridge
    ports:
      - "3001:3000"
    environment:
      - API_KEY=sk-xxxx
      - HTTPS_PROXY=http://host:prot
      - ACCOUNT_GCPA={"PROJECT_ID":"xxxx","CLIENT_ID":"xxx","CLIENT_SECRET":"xxx","REFRESH_TOKEN":"xxxx"}
      - ACCOUNT_GCPB={"type":"service_account","project_id":"xxx","private_key_id":"xxx","private_key":"xxx","client_email":"xxx","client_id":"xxx","auth_uri":"xxx","token_uri":"xxx","auth_provider_x509_cert_url":"xxx","client_x509_cert_url":"xxx","universe_domain":"googleapis.com"}
```

### 环境变量说明
* HTTPS_PROXY: 指定所有请求通过的代理服务器的URL
* ACCOUNT_XX（下面的方式二选一就行）: 
  * 获取私钥信息（推荐）参考: https://github.com/MartialBE/one-api/wiki/VertexAI
  * 获取REFRESH_TOKEN参考: https://linux.do/t/topic/118702#gcp-1
* API_KEY: 调用接口时的鉴权Token
