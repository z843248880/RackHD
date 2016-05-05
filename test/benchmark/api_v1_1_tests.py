import time

from proboscis.asserts import assert_equal
from proboscis.asserts import assert_false
from proboscis import SkipTest
from proboscis import test
from proboscis import before_class
from proboscis import after_class
from json import loads

from modules.logger import Log
from modules.amqp import AMQPWorker
from modules.worker import WorkerThread, WorkerTasks
from config.api1_1_config import *
from config.amqp import *
from on_http_api1_1 import NodesApi as Nodes
from on_http_api1_1 import WorkflowApi as Workflows
from tests.api.v1_1.discovery_tests import DiscoveryTests
from tests.api.v1_1.poller_tests import PollerTests

from benchmark import ansible_ctl
from benchmark.utils import parser
from benchmark.utils.case_recorder import caseRecorder

LOG = Log(__name__)

class BenchmarkTests(object):
    def __init__(self, name):
        ansible_ctl.render_case_name(name)
        self.__data_path = ansible_ctl.get_data_path_per_case()
        self.case_recorder = caseRecorder(self.__data_path)
        self.client = config.api_client
        self.node_count = 0

    def prepare_case_env(self):
        self.node_count = self.check_compute_count()
        self.case_recorder.write_interval(ansible_ctl.get_data_interval())
        self.case_recorder.write_start()
        self.case_recorder.write_node_number(self.node_count)

        assert_equal(True, ansible_ctl.start_daemon(), \
                    message='Failed to start data collection daemon!')

    def collect_case_data(self):
        assert_equal(True, ansible_ctl.collect_data(), message='Failed to collect footprint data!')
        self.case_recorder.write_end()

        # parser.parse(self.__data_path)

    def check_compute_count(self):
        Nodes().nodes_get()
        nodes = loads(self.client.last_response.data)
        count = 0
        for n in nodes:
            type = n.get('type')
            if type == 'compute':
                count += 1
        return count


@test(groups=["benchmark.poller"])
class BenchmarkPollerTests(BenchmarkTests):
    def __init__(self):
        BenchmarkTests.__init__(self,'poller')

    @test(groups=["test-bm-poller"], depends_on_groups=["test-node-poller"])
    def test_poller(self):
        """ Wait for 15 mins to let RackHD run pollers """
        self.prepare_case_env()
        time.sleep(900)
        self.collect_case_data()
        LOG.info('Fetch poller log finished')

@test(groups=["benchmark.discovery"])
class BenchmarkDiscoveryTests(BenchmarkTests):
    def __init__(self):
        BenchmarkTests.__init__(self,'discovery')
        self.__task = None
        self.__discovered = 0

    @test(groups=["test-bm-discovery-prepare"], depends_on_groups=["test-node-poller"])
    def test_prepare_discovery(self):
        """ Prepare discovery """
        self.prepare_case_env()

    @test(groups=["test-bm-discovery"],
            depends_on_groups=["test-bm-discovery-prepare", "test_discovery_delete_node"])
    def test_discovery(self):
        """ Wait for discovery finished """
        self.case_recorder.write_event('start all discovery')
        self.__task = WorkerThread(AMQPWorker(queue=QUEUE_GRAPH_FINISH, \
                                              callbacks=[self.handle_discovery_finish]), \
                                   'benchmark discovery')
        def start(worker, id):
            worker.start()
        tasks = WorkerTasks(tasks=[self.__task], func=start)
        tasks.run()
        tasks.wait_for_completion(timeout_sec=1200)
        assert_false(self.__task.timeout, \
            message='timeout waiting for task {0}'.format(self.__task.id))

    def handle_discovery_finish(self, body, message):
        routeId = message.delivery_info.get('routing_key').split('graph.finished.')[1]
        Workflows().workflows_get()
        workflows = loads(self.client.last_response.data)

        for w in workflows:
            definition = w['definition']
            injectableName = definition.get('injectableName')
            if injectableName == "Graph.SKU.Discovery":
                graphId = w['context'].get('graphId')
                if graphId == routeId:
                    status = body.get('status')
                    if status == 'succeeded':
                        message.ack()
                        self.__discovered += 1
                        self.case_recorder.write_event('finish discovery {0}'
                                .format(self.__discovered))
                        break

        if self.node_count == self.__discovered:
            self.__task.worker.stop()
            self.__task.running = False
            self.__discovered = 0
            self.collect_case_data()
            LOG.info('Fetch discovery log finished')

@test(groups=["benchmark.bootstrap"])
class BenchmarkBootstrapTests(BenchmarkTests):
    def __init__(self):
        BenchmarkTests.__init__(self,'bootstrap')
        self.__task_worker = None
        self.__discovered = 0

    @test(groups=["test-bm-bootstrap-prepare"], depends_on_groups=["test-node-poller"])
    def test_prepare_bootstrap(self):
        pass
