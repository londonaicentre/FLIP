###########
Deploy FLIP
###########

A FLIP deployment consists of a **Central Hub** running in AWS and one or more **FLIP nodes** running inside participating Trusts. Trust-side nodes can run on Trust-managed infrastructure or inside a Trusted Research Environment (TRE). In every model, the trust polls the Central Hub for tasks — all communication is outbound from the trust, no inbound ports are opened on the trust host.

.. toctree::
   :maxdepth: 2

   deploy-flip/deploy-central-hub
   deploy-flip/deploy-flip-node-on-prem
   deploy-flip/deploy-flip-node-in-tre
