- name: Deploy to k3s via SSH
  if: github.ref == 'refs/heads/main'
  uses: appleboy/ssh-action@v0.1.2
  with:
    host: ${{ secrets.DEVICE_HOST }}
    username: ${{ secrets.DEVICE_USER }}
    key: ${{ secrets.DEVICE_SSH_KEY }}
    script: |
      sudo k3s kubectl set image deployment/ared-k3s-app ared=ghcr.io/alvin-muchelle/ared-k3s-app:v1.0.${{ github.run_number }} -n default
      sudo k3s kubectl rollout status deployment/ared-k3s-app -n default
