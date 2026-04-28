import Response from '@/lib/response/Response.ts';
import util from '@/lib/util.ts';
import { request } from '../controllers/core.ts';

export default {
    prefix: '/v1',
    post: {
        '/history/query': async (ctx: any) => {
            const { history_ids, submit_ids, token } = ctx.request.body;
            if (!token || !history_ids) {
                throw new Error('Missing required fields: token, history_ids');
            }
            const result = await request(
                'post',
                '/mweb/v1/get_history_by_ids',
                token,
                {
                    data: {
                        history_ids: history_ids,
                        submit_ids: submit_ids || history_ids,
                    },
                }
            );
            return new Response(result);
        },
    },
};
