###########
Deploy FLIP
###########

A production FLIP deployment has two halves:

- one **Central Hub**, run in AWS by the FLIP administrators: :doc:`deploy-flip/deploy-central-hub`;
- a **trust node** at each participating trust, on the trust's own infrastructure or inside a Trusted Research
  Environment: :doc:`deploy-flip/deploy-trust-node`.

**All traffic is outbound from the trust.** A trust node polls the Central Hub for work; no inbound port is opened on
the trust host for the hub.

To run the whole platform on one machine for development, see :doc:`deploy-flip/deploy-local-dev`.

.. toctree::
   :maxdepth: 2

   deploy-flip/deploy-central-hub
   deploy-flip/deploy-trust-node
   deploy-flip/deploy-local-dev
